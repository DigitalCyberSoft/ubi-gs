import sys, os, errno, re, signal, socket, subprocess, tempfile, textwrap, unittest
# relative module import stuff
root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
homm5_dir = os.path.join(root_dir, 'homm5')
sys.path.append(root_dir)
sys.path.append(homm5_dir)
import ports, firewall, launch

def os_error(code: int, winerror: int = None):
  err = OSError(code, os.strerror(code) if code < 1000 else 'Winsock error')
  if winerror is not None:
    err.winerror = winerror
  return err

class MessageTests(unittest.TestCase):
  """Bind errors map to messages naming the service, port, reason and fix."""
  def test_in_use_linux(self):
    msg = ports.bind_error_message('router', 'tcp', ('0.0.0.0', 7777), os_error(errno.EADDRINUSE), platform='linux', owner='PID 42, nginx')
    self.assertIn("router can't listen on TCP port 7777", msg)
    self.assertIn('already using it (PID 42, nginx)', msg)
    self.assertIn("ss -ltnp 'sport = :7777'", msg)

  def test_in_use_udp(self):
    msg = ports.bind_error_message('cdkey', 'udp', ('0.0.0.0', 7780), os_error(errno.EADDRINUSE), platform='linux')
    self.assertIn('CD key can\'t listen on UDP port 7780', msg)
    self.assertIn("ss -lunp 'sport = :7780'", msg)

  def test_root_needed_linux(self):
    msg = ports.bind_error_message('gsconnect', 'tcp', ('0.0.0.0', 80), os_error(errno.EACCES), platform='linux')
    self.assertIn('TCP port 80', msg)
    self.assertIn('need root', msg)
    self.assertIn('setcap cap_net_bind_service', msg)
    self.assertIn('venv --copies', msg)
    self.assertIn('reverse proxy', msg)

  def test_windows_in_use(self):
    msg = ports.bind_error_message('lobby', 'tcp', ('0.0.0.0', 7785), os_error(10048, ports.WSAEADDRINUSE), platform='win32')
    self.assertIn('already using it', msg)
    self.assertIn('netstat -ano -p TCP | findstr :7785', msg)
    self.assertIn('tasklist', msg)

  def test_windows_access_port_80(self):
    msg = ports.bind_error_message('gsconnect', 'tcp', ('0.0.0.0', 80), os_error(10013, ports.WSAEACCES), platform='win32')
    self.assertIn('HTTP.sys', msg)
    self.assertIn('World Wide Web Publishing Service', msg)
    self.assertIn('Hyper-V/WSL/Docker', msg)
    self.assertIn('netsh http show servicestate', msg)
    self.assertIn('netsh int ipv4 show excludedportrange protocol=tcp', msg)

  def test_windows_access_udp(self):
    msg = ports.bind_error_message('nat', 'udp', ('0.0.0.0', 7781), os_error(10013, ports.WSAEACCES), platform='win32')
    self.assertIn('Hyper-V/WSL/Docker', msg)
    self.assertIn('excludedportrange protocol=udp', msg)
    self.assertNotIn('HTTP.sys', msg)

  def test_wrong_address(self):
    msg = ports.bind_error_message('router', 'tcp', ('192.0.2.1', 7777), os_error(errno.EADDRNOTAVAIL), platform='linux')
    self.assertIn('192.0.2.1 is not on this machine', msg)

  def test_one_line(self):
    for code, win in [(errno.EADDRINUSE, None), (errno.EACCES, None), (10013, 10013), (10048, 10048)]:
      msg = ports.bind_error_message('gsconnect', 'tcp', ('0.0.0.0', 80), os_error(code, win), platform='win32' if win else 'linux')
      self.assertNotIn('\n', msg)

class OwnerTests(unittest.TestCase):
  def test_netstat(self):
    out = textwrap.dedent('''
      Active Connections

        Proto  Local Address          Foreign Address        State           PID
        TCP    0.0.0.0:80             0.0.0.0:0              LISTENING       4
        TCP    0.0.0.0:7777           0.0.0.0:0              LISTENING       5120
        TCP    127.0.0.1:7777         127.0.0.1:50000        ESTABLISHED     5120
    ''')
    self.assertEqual(ports._netstat_owner('tcp', 7777, out), 'PID 5120')
    self.assertIn('HTTP.sys', ports._netstat_owner('tcp', 80, out))
    self.assertIsNone(ports._netstat_owner('tcp', 7785, out))
    udp = '  UDP    0.0.0.0:7780           *:*                                    888\n'
    self.assertEqual(ports._netstat_owner('udp', 7780, udp), 'PID 888')

  @unittest.skipUnless(os.path.isdir('/proc/net'), 'Linux only')
  def test_proc(self):
    with socket.socket() as sock:
      sock.bind(('127.0.0.1', 0))
      sock.listen()
      owner = ports.port_owner('tcp', sock.getsockname()[1])
    self.assertIn(f'PID {os.getpid()}', owner)

def run_service(script: str, endpoint: str, port: int, args: list = []):
  """Runs a service with its endpoint moved to `port`, returns (exit code, output)."""
  code = textwrap.dedent(f'''
    import os, runpy, sys
    sys.path[:0] = [{root_dir!r}, {homm5_dir!r}]
    import h5
    h5.ENDPOINTS[{endpoint!r}] = ('127.0.0.1', {port})
    sys.argv = [{script!r}] + {args!r}
    runpy.run_path({script!r}, run_name='__main__')
  ''')
  env = dict(os.environ, UBIGS_LAUNCHED='1')
  res = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True, timeout=20, env=env, cwd=os.path.dirname(script))
  return res.returncode, res.stdout + res.stderr

class ServiceTests(unittest.TestCase):
  """A service whose port is taken says so and exits with an error."""
  def occupied(self, proto: str):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM if proto == 'udp' else socket.SOCK_STREAM)
    sock.bind(('127.0.0.1', 0))
    if proto == 'tcp':
      sock.listen()
    self.addCleanup(sock.close)
    return sock.getsockname()[1]

  def check(self, script, endpoint, proto, name, args_for=None):
    port = self.occupied(proto)
    args = args_for(port) if args_for else []
    code, out = run_service(os.path.join(root_dir, script), endpoint, port, args)
    self.assertEqual(code, ports.EXIT_BIND, out)
    self.assertIn(f"{name} can't listen on {proto.upper()} port {port}: another program is already using it", out)
    self.assertNotIn('Traceback', out)

  def test_router(self):
    self.check('homm5/router.py', 'router', 'tcp', 'router')

  def test_lobby(self):
    self.check('homm5/lobby.py', 'lobby', 'tcp', 'lobby')

  def test_cdkey(self):
    self.check('homm5/cdkey.py', 'cdkey', 'udp', 'CD key')

  def test_gsnat(self):
    self.check('homm5/gsnat.py', 'nat', 'udp', 'NAT')

  def test_irc(self):
    self.check('homm5/gsirc.py', 'irc', 'tcp', 'IRC chat')

  def test_gsconnect(self):
    self.check('gsconnect/gsconnect.py', 'router', 'tcp', 'gsconnect', lambda port: ['--host', '127.0.0.1', '--port', str(port)])

class LauncherTests(unittest.TestCase):
  """The launcher names a service that failed to start and stops the others."""
  def test_failed_service(self):
    with tempfile.TemporaryDirectory() as tmp:
      marker = os.path.join(tmp, 'stopped')
      with open(os.path.join(tmp, 'ok.py'), 'w') as f:
        f.write(f'import signal, sys, time\n'
          f'def bye(*_):\n  open({marker!r}, "w").close(); sys.exit(0)\n'
          f'signal.signal(signal.SIGTERM, bye)\nprint("ok is listening")\nwhile True: time.sleep(0.1)\n')
      with open(os.path.join(tmp, 'bad.py'), 'w') as f:
        f.write('import sys, time\ntime.sleep(0.5)\n'
          'print("bad can\'t listen on TCP port 7777: another program is already using it.", file=sys.stderr)\nsys.exit(2)\n')
      here = launch.HERE
      launch.HERE = tmp
      handlers = signal.getsignal(signal.SIGINT), signal.getsignal(signal.SIGTERM)
      self.addCleanup(signal.signal, signal.SIGINT, handlers[0])
      self.addCleanup(signal.signal, signal.SIGTERM, handlers[1])
      read, write = os.pipe()
      saved = os.dup(1)
      try:
        os.dup2(write, 1)
        code = launch.main(['ok', 'bad'], firewall_check=lambda: None)
      finally:
        sys.stdout.flush()
        os.dup2(saved, 1)
        os.close(write)
        launch.HERE = here
      out = os.read(read, 65536).decode()
      os.close(read)
      self.assertEqual(code, 2)
      self.assertIn("launch: bad failed to start (exit code 2): bad can't listen on TCP port 7777", out)
      self.assertTrue(os.path.exists(marker), 'the other service was not stopped')

NETSH_ALLOW_PYTHON = r'''
Rule Name:                            Python
----------------------------------------------------------------------
Enabled:                              Yes
Direction:                            In
Profiles:                             Private,Public
Grouping:
LocalIP:                              Any
RemoteIP:                             Any
Protocol:                             TCP
LocalPort:                            Any
RemotePort:                           Any
Edge traversal:                       No
Program:                              C:\Python311\python.exe
InterfaceTypes:                       Any
Security:                             NotRequired
Rule source:                          Local Setting
Action:                               Allow

Rule Name:                            Python
----------------------------------------------------------------------
Enabled:                              Yes
Direction:                            In
Profiles:                             Private,Public
Protocol:                             UDP
LocalPort:                            Any
Program:                              C:\Python311\python.exe
Action:                               Allow
Ok.
'''

# the TCP rule for python.exe blocks (as when Windows' "allow access" prompt is cancelled)
NETSH_BLOCK_PYTHON = NETSH_ALLOW_PYTHON.replace('Action:                               Allow\n\nRule Name', 'Action:                               Block\n\nRule Name', 1)

NETSH_TCP_PORTS = r'''
Rule Name:                            ubi-gs TCP
----------------------------------------------------------------------
Enabled:                              Yes
Direction:                            In
Protocol:                             TCP
LocalPort:                            80,7777,7779,7782-7785
Program:                              Any
Action:                               Allow

Rule Name:                            Old rule
----------------------------------------------------------------------
Enabled:                              No
Direction:                            In
Protocol:                             UDP
LocalPort:                            7780-7781
Program:                              Any
Action:                               Allow
Ok.
'''

STATE_ON = 'Domain Profile Settings:\n----------------------------------------------------------------------\nState                                 ON\nOk.\n'
STATE_OFF = STATE_ON.replace(' ON', ' OFF')
PROGRAMS = [r'C:\Python311\python.exe']

class WindowsFirewallTests(unittest.TestCase):
  """Windows Defender Firewall rules, as `netsh` prints them."""
  def setUp(self):
    self.ports = ports.all_ports()

  def test_parse(self):
    rules = firewall.parse_netsh_rules(NETSH_TCP_PORTS)
    self.assertEqual([r['rule name'] for r in rules], ['ubi-gs TCP', 'Old rule'])
    self.assertEqual(rules[0]['localport'], '80,7777,7779,7782-7785')

  def test_python_allowed(self):
    self.assertIsNone(firewall.windows_warning(self.ports, NETSH_ALLOW_PYTHON, STATE_ON, PROGRAMS))

  def test_other_python(self):
    warning = firewall.windows_warning(self.ports, NETSH_ALLOW_PYTHON, STATE_ON, [r'C:\Other\python.exe'])
    self.assertIn('TCP 7777 (router): no rule allows it', warning)

  def test_python_blocked(self):
    warning = firewall.windows_warning(self.ports, NETSH_BLOCK_PYTHON, STATE_ON, PROGRAMS)
    self.assertIn('blocked by rule "Python"', warning)
    self.assertIn('TCP 80 (gsconnect)', warning)
    self.assertNotIn('UDP 7780', warning)
    self.assertIn('disable the blocking rules', warning)

  def test_udp_missing(self):
    warning = firewall.windows_warning(self.ports, NETSH_TCP_PORTS, STATE_ON, PROGRAMS)
    self.assertIn('UDP 7780 (cdkey): no rule allows it', warning)
    self.assertIn('UDP 7781 (nat)', warning)
    self.assertNotIn('TCP 7777', warning)
    self.assertIn('netsh advfirewall firewall add rule name="ubi-gs UDP" dir=in action=allow protocol=UDP localport=7780,7781', warning)
    self.assertNotIn('protocol=TCP', warning)
    self.assertIn('administrator', warning)

  def test_firewall_off(self):
    self.assertIsNone(firewall.windows_warning(self.ports, '', STATE_OFF, PROGRAMS))

  def test_rules_unreadable(self):
    warning = firewall.windows_warning(self.ports, None, None, PROGRAMS)
    self.assertIn('could not read', warning)
    self.assertIn('TCP: 80,7777,7779,7782,7783,7784,7785', warning)
    self.assertIn('UDP: 7780,7781', warning)
    self.assertIn('protocol=TCP localport=80,7777,7779,7782,7783,7784,7785', warning)
    self.assertIn('protocol=UDP localport=7780,7781', warning)

class LinuxFirewallTests(unittest.TestCase):
  def setUp(self):
    self.ports = ports.homm5_ports()

  def test_firewalld(self):
    missing = firewall.firewalld_missing(self.ports, '7777/tcp 7779-7785/tcp 7780/udp', 'ssh http')
    self.assertEqual([(p, proto) for _, proto, p in missing], [(7781, 'udp')])
    hint = firewall.linux_hint(self.ports, missing, 'firewalld')
    self.assertIn('--add-port=7781/udp', hint)
    self.assertNotIn('\n', hint)

  def test_ufw(self):
    status = 'Status: active\n\nTo                         Action      From\n--                         ------      ----\n7777/tcp                   ALLOW       Anywhere\n7780                       ALLOW       Anywhere\n'
    missing = firewall.ufw_missing(self.ports, status)
    self.assertNotIn(7777, [p for _, _, p in missing])
    self.assertNotIn(7780, [p for _, _, p in missing])
    self.assertIn(7785, [p for _, _, p in missing])
    self.assertIn('sudo ufw allow 7785/tcp', firewall.linux_hint(self.ports, missing, 'ufw'))

  def test_check_never_raises(self):
    firewall.check(self.ports)

IPTABLES_FEDORA = """-P INPUT ACCEPT
-P FORWARD ACCEPT
-P OUTPUT ACCEPT
-A INPUT -m state --state RELATED,ESTABLISHED -j ACCEPT
-A INPUT -p icmp -j ACCEPT
-A INPUT -i lo -j ACCEPT
-A INPUT -p tcp -m state --state NEW -m tcp --dport 22 -j ACCEPT
-A INPUT -p tcp -m multiport --dports 80,7777,7779 -j ACCEPT
-A INPUT -j REJECT --reject-with icmp-host-prohibited
"""

IPTABLES_DEBIAN = """-P INPUT DROP
-P FORWARD DROP
-P OUTPUT ACCEPT
-N game
-A INPUT -i lo -j ACCEPT
-A INPUT -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT
-A INPUT -j game
-A game -p tcp -m tcp --dport 7777:7785 -j ACCEPT
-A game -p udp -m udp --dport 7780 -j ACCEPT
"""

IPTABLES_SAVE = """# Generated by iptables-save
*filter
:INPUT DROP [0:0]
:FORWARD DROP [0:0]
:OUTPUT ACCEPT [0:0]
-A INPUT -i lo -j ACCEPT
-A INPUT -p tcp --dport 80 -j ACCEPT
COMMIT
"""

NFT_RULESET = """table inet filter {
	chain input {
		type filter hook input priority filter; policy drop;
		ct state established,related accept
		iif "lo" accept
		tcp dport 22 accept
		tcp dport { 80, 7777-7785 } accept
		jump game
	}
	chain game {
		udp dport 7780 accept
	}
	chain forward {
		type filter hook forward priority filter; policy drop;
	}
}
"""

NFT_OPEN = """table inet filter {
	chain input {
		type filter hook input priority 0; policy accept;
	}
}
"""

class RawLinuxFirewallTests(unittest.TestCase):
  """Plain iptables and nftables rules (no ufw/firewalld)."""
  def setUp(self):
    self.ports = ports.all_ports()

  def missing(self, found):
    return sorted((p, proto) for _, proto, p in found)

  def test_iptables_final_reject(self):
    # RHEL/Fedora style: policy ACCEPT, then a catch-all REJECT
    found = firewall.iptables_missing(self.ports, IPTABLES_FEDORA)
    self.assertEqual(self.missing(found), [(7780, 'udp'), (7781, 'udp'), (7782, 'tcp'), (7783, 'tcp'), (7784, 'tcp'), (7785, 'tcp')])

  def test_iptables_drop_policy_and_user_chain(self):
    found = firewall.iptables_missing(self.ports, IPTABLES_DEBIAN)
    self.assertEqual(self.missing(found), [(80, 'tcp'), (7781, 'udp')])

  def test_iptables_save_format(self):
    found = firewall.iptables_missing(self.ports, IPTABLES_SAVE)
    self.assertNotIn((80, 'tcp'), self.missing(found))
    self.assertIn((7777, 'tcp'), self.missing(found))
    self.assertIn((7780, 'udp'), self.missing(found))

  def test_iptables_open(self):
    self.assertEqual(firewall.iptables_missing(self.ports, '-P INPUT ACCEPT\n-P FORWARD ACCEPT\n'), [])

  def test_nft(self):
    found, where = firewall.nft_missing(self.ports, NFT_RULESET)
    self.assertEqual(self.missing(found), [(7781, 'udp')])
    self.assertEqual(where, [('inet filter', 'input')])
    hint = firewall.raw_firewall_hint(found, where)
    self.assertIn('sudo iptables -I INPUT -p udp -m multiport --dports 7781 -j ACCEPT', hint)
    self.assertIn('sudo nft add rule inet filter input udp dport { 7781 } accept', hint)
    self.assertNotIn('-p tcp', hint)

  def test_nft_open(self):
    self.assertEqual(firewall.nft_missing(self.ports, NFT_OPEN), ([], []))

  def test_hint_lists_both_protocols(self):
    hint = firewall.raw_firewall_hint(firewall.iptables_missing(self.ports, IPTABLES_FEDORA))
    self.assertIn('-p tcp -m multiport --dports 7782,7783,7784,7785 -j ACCEPT', hint)
    self.assertIn('-p udp -m multiport --dports 7780,7781 -j ACCEPT', hint)
    self.assertIn('nft add rule inet filter input tcp dport { 7782, 7783, 7784, 7785 } accept', hint)

  def run_check(self, results: dict, files: dict = {}):
    """`check_raw_linux` with mocked commands (cmd[0] -> (code, out, err) or None) and saved files."""
    run, read = firewall._run_full, firewall._read
    firewall._run_full = lambda cmd: results.get(cmd[0])
    firewall._read = lambda path: files.get(path)
    try:
      return firewall.check_raw_linux(self.ports)
    finally:
      firewall._run_full, firewall._read = run, read

  def test_needs_root(self):
    hint = self.run_check({
      'iptables': (4, '', 'iptables v1.8.10 (nf_tables): Could not fetch rule set generation id: Permission denied (you must be root)\n'),
      'nft': (1, '', 'Operation not permitted (you must be root)\n')})
    self.assertIn('needs root', hint)
    self.assertIn('sudo iptables -S INPUT; sudo nft list ruleset', hint)
    self.assertIn('UDP 7780,7781', hint)

  def test_root_reads_rules(self):
    hint = self.run_check({'iptables': (0, IPTABLES_DEBIAN, '')})
    self.assertIn('80/tcp', hint)
    self.assertIn('7781/udp', hint)

  def test_saved_ruleset(self):
    hint = self.run_check({'iptables': (4, '', 'Permission denied (you must be root)')}, {'/etc/nftables.conf': NFT_RULESET})
    self.assertIn('saved in /etc/nftables.conf', hint)
    self.assertIn('7781/udp', hint)

  def test_no_tools(self):
    self.assertIsNone(self.run_check({}))

class ReadmeTests(unittest.TestCase):
  """The README's port table matches the ports the services use."""
  def test_port_table(self):
    with open(os.path.join(root_dir, 'README.md'), encoding='utf-8') as f:
      readme = f.read()
    table = set(re.findall(r'^ *\| (\d+) \| (TCP|UDP) \|', readme, re.M))
    expected = {(str(port), proto.upper()) for _, proto, port in ports.all_ports()}
    self.assertEqual(table, expected)

if __name__ == '__main__':
  unittest.main()
