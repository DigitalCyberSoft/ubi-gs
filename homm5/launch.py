"""
Starts all Heroes of Might and Magic V services and stops them together (Ctrl+C).
Works on Linux, macOS and Windows; `homm5.bat` does the same in separate windows on Windows.
Each service's output is prefixed with its name. If a service can't start (e.g. its port is taken),
the launcher says which one and why, stops the others and exits with an error.
"""
import os, signal, subprocess, sys, threading, time

SERVICES = ['gsnat', 'gsirc', 'router', 'cdkey', 'router_wm', 'proxy', 'proxy_wm', 'lobby']
"""Service scripts in this directory, in `homm5.bat` order."""

HERE = os.path.dirname(os.path.abspath(__file__))

STARTUP = 2.0
"""Seconds after which all services are taken as started."""

def relay(name: str, proc: subprocess.Popen, last: dict, width: int):
  """Prints a service's output lines with its name and remembers the last one."""
  for line in proc.stdout:
    if line.strip():
      last[name] = line.strip()
    print(f'{name:<{width}} | {line}', end='', flush=True)

def stop(procs: dict):
  for proc in procs.values():
    if proc.poll() is None:
      proc.terminate()
  for proc in procs.values():
    try:
      proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
      proc.kill()

def check_firewall():
  """One firewall check for all the services' ports (the services skip theirs when launched)."""
  sys.path.append(os.path.join(HERE, '..'))
  try:
    import firewall, ports
    firewall.warn(ports.homm5_ports())
  except Exception as err:
    print(f'launch: could not check the firewall: {err!r}', flush=True)

def main(services: list[str] = SERVICES, firewall_check=check_firewall):
  procs, relays, last = {}, {}, {}
  width = max(len(s) for s in services)
  env = dict(os.environ, UBIGS_LAUNCHED='1')
  for name in services:
    procs[name] = subprocess.Popen([sys.executable, '-u', os.path.join(HERE, f'{name}.py')], cwd=HERE, env=env,
      stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors='replace')
    relays[name] = threading.Thread(target=relay, args=(name, procs[name], last, width), daemon=True)
    relays[name].start()
  # SIGTERM (e.g. systemd, docker stop) stops everything like Ctrl+C
  signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt))
  started = time.monotonic()
  checked = False
  try:
    while True:
      for name, proc in procs.items():
        if proc.poll() is not None:
          relays[name].join(timeout=2)
          what = 'failed to start' if time.monotonic() - started < STARTUP + 1 else 'stopped'
          reason = last.get(name, 'no output')
          print(f'launch: {name} {what} (exit code {proc.returncode}): {reason}', flush=True)
          print('launch: stopping the other services', flush=True)
          return proc.returncode or 1
      if not checked and time.monotonic() - started >= STARTUP:
        checked = True
        print(f'launch: all {len(procs)} services started', flush=True)
        firewall_check()
      time.sleep(0.2)
  except KeyboardInterrupt:
    return 0
  finally:
    stop(procs)

if __name__ == '__main__':
  sys.exit(main())
