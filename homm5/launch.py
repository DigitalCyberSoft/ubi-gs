"""
Starts all Heroes of Might and Magic V services and stops them together (Ctrl+C).
Works on Linux, macOS and Windows; `homm5.bat` does the same in separate windows on Windows.
Each service's output is prefixed with its name.
"""
import os, signal, subprocess, sys, threading, time

SERVICES = ['gsnat', 'gsirc', 'router', 'cdkey', 'router_wm', 'proxy', 'proxy_wm', 'lobby']
"""Service scripts in this directory, in `homm5.bat` order."""

HERE = os.path.dirname(os.path.abspath(__file__))

def relay(name: str, proc: subprocess.Popen):
  """Prints a service's output lines with its name."""
  width = max(len(s) for s in SERVICES)
  for line in proc.stdout:
    print(f'{name:<{width}} | {line}', end='', flush=True)

def main():
  procs = {}
  for name in SERVICES:
    procs[name] = subprocess.Popen([sys.executable, '-u', os.path.join(HERE, f'{name}.py')], cwd=HERE,
      stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors='replace')
    threading.Thread(target=relay, args=(name, procs[name]), daemon=True).start()
  # SIGTERM (e.g. systemd, docker stop) stops everything like Ctrl+C
  signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt))
  try:
    while True:
      for name, proc in procs.items():
        if proc.poll() is not None:
          print(f'{name} exited with code {proc.returncode}, stopping the others', flush=True)
          return 1
      time.sleep(0.5)
  except KeyboardInterrupt:
    return 0
  finally:
    for proc in procs.values():
      if proc.poll() is None:
        proc.terminate()
    for proc in procs.values():
      try:
        proc.wait(timeout=5)
      except subprocess.TimeoutExpired:
        proc.kill()

if __name__ == '__main__':
  sys.exit(main())
