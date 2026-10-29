import sys, os, socket, threading, time, unittest
from unittest import mock
# relative module import stuff
root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.append(os.path.join(root_dir, 'gsconnect'))
import gsconnect

class GSConnectTests(unittest.TestCase):
  """Tests for the gsconnect web service."""
  def setUp(self):
    self.client = gsconnect.app.test_client()

  def test_homm5_servers(self):
    res = self.client.get('/gsinit.php?dp=HEROES_657d2c2ebadc6a1d')
    self.assertEqual(res.status_code, 200)
    self.assertIn(b'NATServerPort0=7781', res.data)

  def test_expansion_servers(self):
    # gsinit ids of Hammers of Fate 2.1 and Tribes of the East 3.1 (the `dp` their H5_Game.exe sends)
    for dp in ['HEROES_f4609f85d388479e', 'HEROES_29988429c481f219']:
      res = self.client.get(f'/gsinit.php?dp={dp}')
      self.assertEqual(res.status_code, 200, dp)
      self.assertIn(b'RouterPort0=7777', res.data)

  def test_no_debugger_console(self):
    self.assertEqual(self.client.get('/console').status_code, 400)
    self.assertFalse(gsconnect.app.debug)

  def test_missing_game_id(self):
    self.assertEqual(self.client.get('/gsinit.php').status_code, 400)

  def test_unknown_game_id(self):
    self.assertEqual(self.client.get('/gsinit.php?dp=NOPE').status_code, 400)

  def test_debug_off_by_default(self):
    sys.argv = ['gsconnect.py']
    args = gsconnect.get_args()
    self.assertFalse(args.debug)
    self.assertEqual(args.host, '0.0.0.0')
    self.assertEqual(args.port, 80)

  def test_env_overrides(self):
    sys.argv = ['gsconnect.py']
    os.environ['GSCONNECT_HOST'], os.environ['GSCONNECT_PORT'] = '127.0.0.1', '8080'
    try:
      args = gsconnect.get_args()
    finally:
      del os.environ['GSCONNECT_HOST'], os.environ['GSCONNECT_PORT']
    self.assertEqual((args.host, args.port), ('127.0.0.1', 8080))

def closed(sock: socket.socket):
  """The server closed the connection (within the socket's timeout)."""
  try:
    return sock.recv(1) == b''
  except ConnectionError:
    return True
  except socket.timeout:
    return False

class ServerLimitTests(unittest.TestCase):
  """gsconnect's server caps connections like the game services do, and drops silent ones."""
  def start(self, max_per_ip: int = 16):
    server = gsconnect.LimitedServer('127.0.0.1', 0, gsconnect.app, gsconnect.limits.ConnectionLimits(10, max_per_ip))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    self.addCleanup(server.server_close)
    self.addCleanup(server.shutdown)
    return server.server_address[1]

  def connect(self, port: int, timeout: float = 2):
    sock = socket.create_connection(('127.0.0.1', port), timeout=timeout)
    self.addCleanup(sock.close)
    return sock

  def test_per_address_cap(self):
    port = self.start(max_per_ip=2)
    held = [self.connect(port) for _ in range(2)]
    time.sleep(0.2)
    # a third from the same address is closed as it arrives
    self.assertTrue(closed(self.connect(port)))
    # closing one makes room again
    held[0].close()
    time.sleep(0.3)
    sock = self.connect(port)
    sock.sendall(b'GET /gsinit.php?dp=HEROES_657d2c2ebadc6a1d HTTP/1.0\r\n\r\n')
    self.assertIn(b' 200 ', sock.recv(64))

  def test_silent_connection_dropped(self):
    with mock.patch.object(gsconnect.Handler, 'timeout', 0.5):
      port = self.start()
      self.assertTrue(closed(self.connect(port, timeout=3)))

if __name__ == '__main__':
  unittest.main()
