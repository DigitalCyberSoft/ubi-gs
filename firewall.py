"""
Best-effort check that the host firewall lets players reach the server's ports. It only warns, it never changes settings.
Windows: Windows Defender Firewall rules (`netsh advfirewall`), where a listening program can still be blocked.
Linux: a short hint when ufw or firewalld is active and the ports aren't open.
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
  return None

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
