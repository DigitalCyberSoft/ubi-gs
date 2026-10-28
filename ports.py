"""
The ports the server listens on, and clear messages when one can't be opened.
All services take their port list from here (`homm5/h5.py` plus gsconnect), so messages and checks stay in sync.
"""
import errno, os, socket, subprocess, sys

ROOT_DIR = os.path.dirname(os.path.abspath(__file__))

GSCONNECT_PORT = 80
"""gsconnect's default port; the game's URL `http://gsconnect.ubisoft.com/gsinit.php` is hard-coded to it."""

UDP_SERVICES = {'cdkey', 'nat'}
"""Entries of `h5.ENDPOINTS` that are UDP services; the rest are TCP."""

NAMES = {
  'gsconnect': 'gsconnect',
  'router': 'router',
  'irc': 'IRC chat',
  'cdkey': 'CD key',
  'nat': 'NAT',
  'router_wm': 'router wait module',
  'proxy': 'proxy',
  'proxy_wm': 'proxy wait module',
  'lobby': 'lobby',
}
"""Names used in messages."""

EXIT_BIND = 2
"""Exit code of a service that could not open its port."""

README = 'README.md, "Running it publicly" and "Troubleshooting: a port is blocked"'

# errno values; Windows reports Winsock codes (in `OSError.winerror`)
WSAEACCES = 10013
WSAEADDRINUSE = 10048
WSAEADDRNOTAVAIL = 10049
ADDR_IN_USE = {errno.EADDRINUSE, WSAEADDRINUSE}
ACCESS = {errno.EACCES, WSAEACCES}
ADDR_NOT_AVAIL = {errno.EADDRNOTAVAIL, WSAEADDRNOTAVAIL}

def homm5_ports():
  """`(service, protocol, port)` of every Heroes V service, from `h5.ENDPOINTS`."""
  homm5_dir = os.path.join(ROOT_DIR, 'homm5')
  if homm5_dir not in sys.path:
    sys.path.append(homm5_dir)
  import h5
  return [(name, 'udp' if name in UDP_SERVICES else 'tcp', port) for name, (_, port) in h5.ENDPOINTS.items()]

def all_ports(gsconnect_port: int = GSCONNECT_PORT):
  """`(service, protocol, port)` of gsconnect and every Heroes V service."""
  return [('gsconnect', 'tcp', gsconnect_port)] + homm5_ports()

def is_windows(platform: str = None):
  return (platform or sys.platform) == 'win32'

def error_code(err: OSError):
  """The Winsock code on Windows, otherwise the errno."""
  return getattr(err, 'winerror', None) or err.errno

def _proc_owner(proto: str, port: int):
  """Linux: the process listening on the port, from /proc (only processes this user may inspect)."""
  inodes = set()
  for table in [f'/proc/net/{proto}', f'/proc/net/{proto}6']:
    try:
      with open(table) as file:
        rows = file.read().splitlines()[1:]
    except OSError:
      continue
    for row in rows:
      cols = row.split()
      if len(cols) < 10:
        continue
      local_port = int(cols[1].rsplit(':', 1)[1], 16)
      # TCP: listening (0A); UDP: bound (07)
      if local_port == port and cols[3] in ('0A', '07'):
        inodes.add(cols[9])
  if not inodes:
    return None
  targets = {f'socket:[{inode}]' for inode in inodes}
  for pid in filter(str.isdigit, os.listdir('/proc')):
    try:
      fds = os.listdir(f'/proc/{pid}/fd')
      if any(os.readlink(f'/proc/{pid}/fd/{fd}') in targets for fd in fds):
        with open(f'/proc/{pid}/comm') as file:
          return f'PID {pid}, {file.read().strip()}'
    except OSError:
      continue
  return 'a process of another user'

def _netstat_owner(proto: str, port: int, netstat_output: str = None):
  """Windows: the PID listening on the port, from `netstat -ano`."""
  if netstat_output is None:
    try:
      netstat_output = subprocess.run(['netstat', '-ano', '-p', proto.upper()], capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
      return None
  for line in netstat_output.splitlines():
    cols = line.split()
    # TCP  0.0.0.0:80  0.0.0.0:0  LISTENING  4    /    UDP  0.0.0.0:7780  *:*  1234
    if len(cols) >= 4 and cols[0].upper() == proto.upper() and cols[1].rsplit(':', 1)[-1] == str(port):
      if proto == 'tcp' and 'LISTEN' not in line.upper():
        continue
      pid = cols[-1]
      return f'PID {pid}' + (', "System", i.e. HTTP.sys' if pid == '4' else '')
  return None

def port_owner(proto: str, port: int, platform: str = None):
  """Best-effort description of the program holding the port, or None."""
  try:
    if is_windows(platform):
      return _netstat_owner(proto, port)
    if os.path.isdir('/proc/net'):
      return _proc_owner(proto, port)
  except Exception:
    pass
  return None

def find_command(proto: str, port: int, platform: str = None):
  """Command that shows which program uses the port."""
  if is_windows(platform):
    return f'netstat -ano -p {proto.upper()} | findstr :{port}  (then: tasklist /FI "PID eq <pid>")'
  if (platform or sys.platform) == 'darwin':
    return f'sudo lsof -nP -i {proto.upper()}:{port}'
  flag = 't' if proto == 'tcp' else 'u'
  return f"sudo ss -l{flag}np 'sport = :{port}'"

def bind_error_message(service: str, proto: str, address: tuple[str, int], err: OSError, platform: str = None, owner: str = None):
  """One line naming the service, protocol and port, why it failed and what to do."""
  host, port = address
  where = f'{NAMES.get(service, service)} can\'t listen on {proto.upper()} port {port}'
  code = error_code(err)
  windows = is_windows(platform)
  if code in ADDR_IN_USE:
    holder = f' ({owner})' if owner else ''
    return (f'{where}: another program is already using it{holder}. '
      f'Stop that program (or the other copy of this service) and start again. Find it with: {find_command(proto, port, platform)}')
  if code in ACCESS and windows:
    tables = 'netsh int ipv4 show excludedportrange protocol=' + proto
    if port == 80 and proto == 'tcp':
      return (f'{where}: Windows refused access. Port 80 is usually held by HTTP.sys (the "System" process: IIS, '
        f'the World Wide Web Publishing Service or another HTTP.sys user), or it is in a port range reserved by Hyper-V/WSL/Docker. '
        f'Check with: netsh http show servicestate  and  {tables}')
    return (f'{where}: Windows refused access. The port is in a range reserved by Hyper-V/WSL/Docker, or another program holds it exclusively. '
      f'Check with: {tables}  and  {find_command(proto, port, platform)}')
  if code in ACCESS:
    if port < 1024:
      return (f'{where}: ports below 1024 need root. Don\'t run the server as root; give a dedicated interpreter the capability '
        f'(python3 -m venv --copies venv; sudo setcap cap_net_bind_service=+ep venv/bin/python3), or use a reverse proxy on port 80. See {README}')
    return f'{where}: permission denied ({err.strerror or err}). A security policy (SELinux, AppArmor, a container) may forbid it.'
  if code in ADDR_NOT_AVAIL:
    return (f'{where}: the address {host} is not on this machine. '
      f'Listen on 0.0.0.0 (all interfaces) or on one of this machine\'s own addresses.')
  return f'{where}: {err.strerror or err}.'

def fail(service: str, proto: str, address: tuple[str, int], err: OSError):
  """Prints why the port can't be opened and exits; the service must not run half-started."""
  owner = port_owner(proto, address[1]) if error_code(err) in ADDR_IN_USE else None
  print(bind_error_message(service, proto, address, err, owner=owner), file=sys.stderr, flush=True)
  sys.exit(EXIT_BIND)

def bind(sock: socket.socket, address: tuple[str, int], service: str):
  """Binds a service's socket, or explains why it can't and exits."""
  proto = 'udp' if sock.type == socket.SOCK_DGRAM else 'tcp'
  # restarting must not wait for old connections to close; on Linux this never allows two listeners
  # (on Windows the option would, so it is left off there)
  if proto == 'tcp' and os.name != 'nt':
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
  try:
    sock.bind(address)
  except OSError as err:
    sock.close()
    fail(service, proto, address, err)
  # binding works even when the host firewall keeps players out; warn about that (never fails)
  import firewall
  firewall.warn_unless_launched([(service, proto, address[1])])

def check_bind(service: str, proto: str, address: tuple[str, int]):
  """Tries to open the port with a throwaway socket, for servers that bind it themselves (gsconnect's Flask)."""
  sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM if proto == 'udp' else socket.SOCK_STREAM)
  bind(sock, address, service)
  sock.close()
