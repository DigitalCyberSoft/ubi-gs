"""
Best-effort check that the host firewall lets players reach the server's ports. It only warns, it never changes settings.
Windows: Windows Defender Firewall rules (`netsh advfirewall`), where a listening program can still be blocked.
Linux: a short hint when ufw, firewalld or plain iptables/nftables rules may keep the ports closed.
"""
import os, re, subprocess, sys

def _run(cmd: list[str]):
  """Output of a command, or None if it can't run."""
  try:
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
  except (OSError, subprocess.SubprocessError):
    return None
  return res.stdout if res.returncode == 0 else None

def _port_list(ports: list[int]):
  return ','.join(str(p) for p in sorted(set(ports)))

# --- Windows -----------------------------------------------------------------

def parse_netsh_rules(output: str):
  """Rules from `netsh advfirewall firewall show rule name=all dir=in verbose` as dicts of lowercase field names."""
  rules, rule = [], None
  for line in output.splitlines():
    if line.startswith('---'):
      continue
    m = re.match(r'^([A-Za-z][A-Za-z ]*?):\s*(.*)$', line)
    if not m:
      continue
    key, value = m.group(1).strip().lower(), m.group(2).strip()
    if key == 'rule name':
      rule = {'rule name': value}
      rules.append(rule)
    elif rule is not None:
      rule[key] = value
  return rules

def _port_matches(spec: str, port: int):
  """`LocalPort` of a rule (`Any`, `80`, `7777-7785`, `80,443`) covers the port."""
  spec = spec.strip()
  if spec.lower() == 'any':
    return True
  for part in spec.split(','):
    part = part.strip()
    if '-' in part:
      lo, hi = part.split('-', 1)
      if lo.isdigit() and hi.isdigit() and int(lo) <= port <= int(hi):
        return True
    elif part.isdigit() and int(part) == port:
      return True
  return False

def _same_program(rule_program: str, programs: list[str]):
  path = os.path.expandvars(rule_program.strip().strip('"'))
  return any(os.path.normcase(path).lower() == os.path.normcase(p).lower() for p in programs)

def _rule_covers(rule: dict, proto: str, port: int, programs: list[str]):
  if rule.get('enabled', '').lower() != 'yes' or rule.get('direction', 'in').lower() != 'in':
    return False
  if rule.get('protocol', 'any').lower() not in ('any', proto):
    return False
  if not _port_matches(rule.get('localport', 'Any'), port):
    return False
  program = rule.get('program', 'Any')
  return program.lower() == 'any' or _same_program(program, programs)

def windows_blocked_ports(ports: list[tuple[str, str, int]], rules: list[dict], programs: list[str]):
  """`(service, proto, port, reason)` of the ports no enabled inbound rule allows, or that a rule blocks."""
  result = []
  for service, proto, port in ports:
    matching = [r for r in rules if _rule_covers(r, proto, port, programs)]
    if any(r.get('action', '').lower() == 'block' for r in matching):
      names = ', '.join(sorted({r['rule name'] for r in matching if r.get('action', '').lower() == 'block'}))
      result.append((service, proto, port, f'blocked by rule "{names}"'))
    elif not any(r.get('action', '').lower() == 'allow' for r in matching):
      result.append((service, proto, port, 'no rule allows it'))
  return result

def windows_firewall_off(state_output: str):
  """`netsh advfirewall show currentprofile state` says the firewall is off."""
  return state_output is not None and re.search(r'^State\s+OFF\s*$', state_output, re.M | re.I) is not None

def windows_add_rule_commands(ports: list[tuple]):
  """`netsh` commands that open the ports, one per protocol."""
  cmds = []
  for proto in ('tcp', 'udp'):
    numbers = [p[2] for p in ports if p[1] == proto]
    if numbers:
      cmds.append(f'netsh advfirewall firewall add rule name="ubi-gs {proto.upper()}" dir=in action=allow protocol={proto.upper()} localport={_port_list(numbers)}')
  return cmds

def windows_warning(ports: list[tuple[str, str, int]], rules_output: str, state_output: str, programs: list[str]):
  """Warning text for Windows, or None if the firewall lets all the ports in."""
  if windows_firewall_off(state_output):
    return None
  rules = parse_netsh_rules(rules_output) if rules_output else []
  if rules:
    blocked = windows_blocked_ports(ports, rules, programs)
    if not blocked:
      return None
    detail = [f'  {proto.upper()} {port} ({service}): {reason}' for service, proto, port, reason in blocked]
    head = 'WARNING: Windows Defender Firewall may block players from reaching these ports:'
  else:
    blocked = [(s, pr, po, '') for s, pr, po in ports]
    tcp = _port_list([p for _, pr, p in ports if pr == 'tcp'])
    udp = _port_list([p for _, pr, p in ports if pr == 'udp'])
    detail = [f'  TCP: {tcp}' if tcp else '', f'  UDP: {udp}' if udp else '']
    head = 'WARNING: could not read the Windows Defender Firewall rules; make sure these ports are open to players:'
  lines = ['', '=' * 78, head] + [d for d in detail if d] + [
    'The server is running, but other players can\'t connect until the firewall lets them in.',
    'To open the ports, run in a command prompt started as administrator:']
  lines += [f'  {cmd}' for cmd in windows_add_rule_commands(blocked)]
  if any('blocked by rule' in b[3] for b in blocked):
    lines.append('and disable the blocking rules above (Windows creates them when its "allow access" prompt is cancelled).')
  lines += ['=' * 78, '']
  return '\n'.join(lines)

def windows_programs():
  """The interpreter files the firewall sees as the listening program (a venv's python.exe starts the base one)."""
  return [p for p in {sys.executable, getattr(sys, '_base_executable', sys.executable)} if p]

def check_windows(ports):
  rules = _run(['netsh', 'advfirewall', 'firewall', 'show', 'rule', 'name=all', 'dir=in', 'verbose'])
  state = _run(['netsh', 'advfirewall', 'show', 'currentprofile', 'state'])
  return windows_warning(ports, rules, state, windows_programs())

# --- Linux -------------------------------------------------------------------

def _active(unit: str):
  out = _run(['systemctl', 'is-active', unit])
  return out is not None and out.strip() == 'active'

def firewalld_missing(ports, open_ports_output: str, services_output: str):
  """Ports firewalld doesn't open (`--list-ports`, plus `http` in `--list-services` for 80/tcp)."""
  opened = set(open_ports_output.split()) if open_ports_output else set()
  services = set(services_output.split()) if services_output else set()
  missing = []
  for service, proto, port in ports:
    if f'{port}/{proto}' in opened or (port == 80 and proto == 'tcp' and 'http' in services):
      continue
    if any('-' in o and o.endswith('/' + proto) and _port_matches(o.split('/')[0], port) for o in opened):
      continue
    missing.append((service, proto, port))
  return missing

def ufw_missing(ports, status_output: str):
  """Ports `ufw status` doesn't show as allowed."""
  missing = []
  for service, proto, port in ports:
    pattern = rf'^{port}(/{proto})?\s+(\(v6\)\s+)?ALLOW'
    if not re.search(pattern, status_output, re.M):
      missing.append((service, proto, port))
  return missing

def linux_hint(ports, missing, tool: str):
  if not missing:
    return None
  spec = ' '.join(f'{p}/{proto}' for _, proto, p in missing)
  if tool == 'firewalld':
    cmd = 'sudo firewall-cmd --permanent ' + ' '.join(f'--add-port={p}/{proto}' for _, proto, p in missing) + ' && sudo firewall-cmd --reload'
  else:
    cmd = '; '.join(f'sudo ufw allow {p}/{proto}' for _, proto, p in missing)
  return f'Note: {tool} is active and may block players from reaching {spec}. To open them: {cmd}'

def check_linux(ports):
  if _active('firewalld'):
    listed = _run(['firewall-cmd', '--list-ports'])
    if listed is None:
      return linux_hint(ports, ports, 'firewalld')
    return linux_hint(ports, firewalld_missing(ports, listed, _run(['firewall-cmd', '--list-services'])), 'firewalld')
  if _active('ufw'):
    status = _run(['ufw', 'status'])
    # reading the rules needs root; then just mention the ports
    return linux_hint(ports, ufw_missing(ports, status) if status else ports, 'ufw')
  return check_raw_linux(ports)

# --- Linux: plain iptables / nftables ---------------------------------------

IPTABLES_SAVED = ['/etc/sysconfig/iptables', '/etc/iptables/rules.v4']
"""Saved iptables rules (iptables-save format) on RHEL/Fedora and Debian/Ubuntu."""

NFT_SAVED = ['/etc/nftables.conf', '/etc/sysconfig/nftables.conf']
"""Saved nftables rulesets on Debian/Ubuntu/Arch and Fedora/RHEL."""

def _run_full(cmd: list[str]):
  """`(returncode, stdout, stderr)` of a command, or None if it isn't installed or can't run."""
  try:
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
  except (OSError, subprocess.SubprocessError):
    return None
  return res.returncode, res.stdout, res.stderr

def _needs_root(stderr: str):
  return re.search(r'permission denied|not permitted|must be root|you must be root', stderr, re.I) is not None

def _iptables_ports(spec: str):
  """`7777`, `7782:7785` or a multiport list `80,7777,7782:7785` as a `_port_matches` spec."""
  return spec.replace(':', '-')

def parse_iptables(output: str):
  """Chains from `iptables -S` or `iptables-save` output: {chain: {'policy': str|None, 'rules': [[args]]}}."""
  chains = {}
  for line in output.splitlines():
    words = line.split()
    if not words:
      continue
    if words[0] == '-P' and len(words) >= 3:
      chains.setdefault(words[1], {'policy': None, 'rules': []})['policy'] = words[2].upper()
    elif words[0] == '-N' and len(words) >= 2:
      chains.setdefault(words[1], {'policy': None, 'rules': []})
    elif words[0].startswith(':') and len(words) >= 2:
      # iptables-save: ":INPUT DROP [0:0]", user chains have "-"
      policy = words[1].upper()
      chains.setdefault(words[0][1:], {'policy': None, 'rules': []})['policy'] = None if policy == '-' else policy
    elif words[0] == '-A' and len(words) >= 2:
      chains.setdefault(words[1], {'policy': None, 'rules': []})['rules'].append(words[2:])
  return chains

def _opt(args: list[str], *names: str):
  for i, word in enumerate(args):
    if word in names and i + 1 < len(args):
      return args[i + 1]
  return None

QUALIFIERS = {'-s', '--source', '-i', '--in-interface', '--state', '--ctstate', '--src-range', '--sport', '--sports', '--source-port', '--mac-source'}
"""Matches that make an ACCEPT rule apply to some traffic only."""

def _iptables_accepts(args: list[str], proto: str, port: int):
  if _opt(args, '-j', '--jump') != 'ACCEPT':
    return False
  rule_proto = _opt(args, '-p', '--protocol')
  if rule_proto not in (None, 'all', proto):
    return False
  ports = _opt(args, '--dport', '--destination-port', '--dports', '--destination-ports')
  if ports is not None:
    return _port_matches(_iptables_ports(ports), port)
  # no port match: only an otherwise unconditional ACCEPT lets the port in
  return not any(word in QUALIFIERS for word in args)

def _iptables_chain_verdict(chains: dict, chain: str, proto: str, port: int, depth: int = 0):
  """'accept', 'drop' or None (falls through) for new traffic to the port in the chain."""
  if depth > 8 or chain not in chains:
    return None
  for args in chains[chain]['rules']:
    if _iptables_accepts(args, proto, port):
      return 'accept'
    target = _opt(args, '-j', '--jump', '-g', '--goto')
    rule_proto = _opt(args, '-p', '--protocol')
    applies = rule_proto in (None, 'all', proto) and not any(word in QUALIFIERS for word in args)
    ports = _opt(args, '--dport', '--destination-port', '--dports', '--destination-ports')
    if ports is not None:
      applies = applies and _port_matches(_iptables_ports(ports), port)
    if target in ('DROP', 'REJECT') and applies:
      return 'drop'
    if target in chains:
      verdict = _iptables_chain_verdict(chains, target, proto, port, depth + 1)
      if verdict is not None:
        return verdict
  return None

def iptables_missing(ports, output: str):
  """Ports an iptables INPUT chain would drop (DROP/REJECT policy or a final catch-all DROP/REJECT)."""
  chains = parse_iptables(output)
  if 'INPUT' not in chains:
    return []
  missing = []
  for service, proto, port in ports:
    verdict = _iptables_chain_verdict(chains, 'INPUT', proto, port)
    if verdict == 'drop' or (verdict is None and chains['INPUT']['policy'] in ('DROP', 'REJECT')):
      missing.append((service, proto, port))
  return missing

def _nft_ports(spec: str):
  """`7777`, `7780-7781` or `{ 80, 7777-7785 }` as a `_port_matches` spec."""
  return spec.strip().strip('{}').replace(' ', '')

def parse_nft(output: str):
  """Chains from `nft list ruleset` / nftables.conf: [{'table', 'name', 'input', 'policy', 'rules': [str]}]."""
  chains, table, chain = [], None, None
  for raw in output.splitlines():
    line = raw.split('#', 1)[0].strip()
    if not line:
      continue
    m = re.match(r'^table\s+(\w+)\s+(\S+)\s*\{', line)
    if m:
      table, chain = f'{m.group(1)} {m.group(2)}', None
      continue
    m = re.match(r'^chain\s+(\S+)\s*\{', line)
    if m and table:
      chain = {'table': table, 'name': m.group(1), 'input': False, 'policy': None, 'rules': []}
      chains.append(chain)
      continue
    if line == '}':
      if chain is not None:
        chain = None
      else:
        table = None
      continue
    if chain is None:
      continue
    if line.startswith('type '):
      chain['input'] = re.search(r'\bhook\s+input\b', line) is not None
      m = re.search(r'\bpolicy\s+(\w+)', line)
      if m:
        chain['policy'] = m.group(1).lower()
      continue
    chain['rules'].append(line)
  return chains

NFT_QUALIFIERS = re.compile(r'\b(iif|iifname|saddr|ct\s+state|sport|ether|meta\s+mark)\b')

def _nft_verdict(chains: list, chain: dict, proto: str, port: int, depth: int = 0):
  if depth > 8:
    return None
  for rule in chain['rules']:
    m = re.search(r'\b(tcp|udp|th)\s+dport\s+(\{[^}]*\}|\S+)', rule)
    proto_ok = True
    if m:
      if m.group(1) != 'th' and m.group(1) != proto:
        proto_ok = False
      elif not _port_matches(_nft_ports(m.group(2)), port):
        proto_ok = False
    l4 = re.search(r'\bl4proto\s+(\{[^}]*\}|\S+)', rule)
    if l4 and proto not in l4.group(1):
      proto_ok = False
    qualified = NFT_QUALIFIERS.search(rule) is not None
    if not proto_ok or qualified:
      continue
    if re.search(r'\baccept\b', rule):
      return 'accept'
    if re.search(r'\b(drop|reject)\b', rule):
      return 'drop'
    jump = re.search(r'\b(jump|goto)\s+(\S+)', rule)
    if jump:
      target = next((c for c in chains if c['table'] == chain['table'] and c['name'] == jump.group(2)), None)
      if target is not None:
        verdict = _nft_verdict(chains, target, proto, port, depth + 1)
        if verdict is not None:
          return verdict
  return None

def nft_missing(ports, output: str):
  """Ports an nftables input-hook chain would drop; returns (missing, [(table, chain)] of those chains)."""
  chains = parse_nft(output)
  missing, where = [], []
  for service, proto, port in ports:
    for chain in (c for c in chains if c['input']):
      verdict = _nft_verdict(chains, chain, proto, port)
      if verdict == 'drop' or (verdict is None and chain['policy'] == 'drop'):
        missing.append((service, proto, port))
        if (chain['table'], chain['name']) not in where:
          where.append((chain['table'], chain['name']))
        break
  return missing, where

def raw_firewall_hint(missing, where: list = None, source: str = ''):
  """Hint with iptables and nft commands that open the missing ports."""
  if not missing:
    return None
  table, chain = where[0] if where else ('inet filter', 'input')
  lines = [f'Note: the firewall rules{source} may block players from reaching ' + ' '.join(f'{p}/{proto}' for _, proto, p in missing) + '. To open them, for example:']
  for proto in ('tcp', 'udp'):
    numbers = sorted({p for _, pr, p in missing if pr == proto})
    if not numbers:
      continue
    lines.append(f'  sudo iptables -I INPUT -p {proto} -m multiport --dports {_port_list(numbers)} -j ACCEPT')
    lines.append(f'  sudo nft add rule {table} {chain} {proto} dport {{ {", ".join(str(n) for n in numbers)} }} accept')
  lines.append('  (and save them the way your distribution does, e.g. iptables-save or /etc/nftables.conf)')
  return '\n'.join(lines)

def needs_root_hint(ports):
  tcp = _port_list([p for _, pr, p in ports if pr == 'tcp'])
  udp = _port_list([p for _, pr, p in ports if pr == 'udp'])
  spec = ' and '.join(x for x in [f'TCP {tcp}' if tcp else '', f'UDP {udp}' if udp else ''] if x)
  return (f'Note: reading the iptables/nftables rules needs root, so the firewall was not checked. '
    f'To check that players can reach {spec}, run: sudo iptables -S INPUT; sudo nft list ruleset')

def _read(path: str):
  try:
    with open(path, encoding='utf-8', errors='replace') as file:
      return file.read()
  except OSError:
    return None

def check_raw_linux(ports):
  """iptables/nftables, when no ufw or firewalld manages them."""
  locked = False
  ipt = _run_full(['iptables', '-S'])
  if ipt is not None:
    code, out, err = ipt
    if code == 0:
      return raw_firewall_hint(iptables_missing(ports, out))
    locked = locked or _needs_root(err)
  nft = _run_full(['nft', 'list', 'ruleset'])
  if nft is not None:
    code, out, err = nft
    if code == 0:
      return raw_firewall_hint(*nft_missing(ports, out))
    locked = locked or _needs_root(err)
  # saved rulesets are often readable without root (they may differ from the active rules)
  for path in IPTABLES_SAVED:
    text = _read(path)
    if text:
      return raw_firewall_hint(iptables_missing(ports, text), source=f' saved in {path}')
  for path in NFT_SAVED:
    text = _read(path)
    if text:
      missing, where = nft_missing(ports, text)
      return raw_firewall_hint(missing, where, source=f' saved in {path}')
  return needs_root_hint(ports) if locked else None

# -----------------------------------------------------------------------------

def check(ports: list[tuple[str, str, int]]):
  """Warning or hint text for the ports, or None. Never raises."""
  try:
    if sys.platform == 'win32':
      return check_windows(ports)
    if sys.platform.startswith('linux'):
      return check_linux(ports)
  except Exception as err:
    return f'Note: could not check the firewall ({err!r}); make sure the ports are open to players.'
  return None

def warn(ports: list[tuple[str, str, int]]):
  """Prints the firewall warning or hint, if there is one."""
  text = check(ports)
  if text:
    print(text, file=sys.stderr, flush=True)

def warn_unless_launched(ports: list[tuple[str, str, int]]):
  """Services started by `homm5/launch.py` leave the firewall check to it (one combined check)."""
  if not os.environ.get('UBIGS_LAUNCHED'):
    warn(ports)
