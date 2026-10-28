import sys, os, importlib, socket, threading, http.client, unittest
# relative module import stuff
root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.append(root_dir)
sys.path.append(os.path.join(root_dir, 'homm5'))
sys.path.append(os.path.join(root_dir, 'gsconnect'))
import h5, gsconnect
from werkzeug.serving import make_server

def local_ip():
  """A non-loopback address of this machine, or None."""
  with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
    try:
      # no packet is sent, this only picks the outgoing interface
      sock.connect(('192.0.2.1', 9))
      ip = sock.getsockname()[0]
    except OSError:
      return None
  return None if ip.startswith('127.') else ip

def accepted(ip: str):
  """Connects to a listener on all interfaces through `ip`, returns the server side socket."""
  server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
  server.bind(('0.0.0.0', 0))
  server.listen(1)
  client = socket.create_connection((ip, server.getsockname()[1]))
  conn, _ = server.accept()
  client.close()
  server.close()
  return conn

class EndpointTests(unittest.TestCase):
  """The game is sent the address it reached the services on, unless UBIGS_HOST is set."""
  def tearDown(self):
    os.environ.pop('UBIGS_HOST', None)
    os.environ.pop('UBIGS_BIND', None)
    importlib.reload(h5)

  def test_listen_all(self):
    importlib.reload(h5)
    self.assertEqual(h5.ENDPOINTS['lobby'], ('0.0.0.0', 7785))

  def test_bind_override(self):
    os.environ['UBIGS_BIND'] = '10.0.0.1'
    importlib.reload(h5)
    self.assertEqual(h5.ENDPOINTS['router'], ('10.0.0.1', 7777))

  def test_loopback(self):
    importlib.reload(h5)
    with accepted('127.0.0.1') as conn:
      self.assertEqual(h5.advertised(h5.ENDPOINTS['proxy_wm'], conn), ('127.0.0.1', 7784))

  def test_other_interface(self):
    ip = local_ip()
    if ip is None:
      self.skipTest('no non-loopback interface')
    importlib.reload(h5)
    with accepted(ip) as conn:
      self.assertEqual(h5.advertised(h5.ENDPOINTS['lobby'], conn), (ip, 7785))

  def test_host_override(self):
    os.environ['UBIGS_HOST'] = 'gs.example.org'
    importlib.reload(h5)
    with accepted('127.0.0.1') as conn:
      self.assertEqual(h5.advertised(h5.ENDPOINTS['proxy'], conn), ('gs.example.org', 7783))

class ServersIniTests(unittest.TestCase):
  """gsconnect fills servers.ini the same way."""
  @classmethod
  def setUpClass(cls):
    cls.server = make_server('0.0.0.0', 0, gsconnect.app)
    cls.port = cls.server.server_port
    threading.Thread(target=cls.server.serve_forever, daemon=True).start()

  @classmethod
  def tearDownClass(cls):
    cls.server.shutdown()

  def tearDown(self):
    os.environ.pop('UBIGS_HOST', None)

  def ini(self, ip: str):
    conn = http.client.HTTPConnection(ip, self.port, timeout=5)
    conn.request('GET', '/gsinit.php?dp=HEROES_657d2c2ebadc6a1d')
    res = conn.getresponse()
    self.assertEqual(res.status, 200)
    data = res.read().decode()
    conn.close()
    return data

  def test_loopback(self):
    data = self.ini('127.0.0.1')
    self.assertIn('RouterIP0=127.0.0.1', data)
    self.assertIn('RouterPort0=7777', data)

  def test_other_interface(self):
    ip = local_ip()
    if ip is None:
      self.skipTest('no non-loopback interface')
    data = self.ini(ip)
    for key in ['RouterIP0', 'IRCIP0', 'CDKeyServerIP0', 'NATServerIP0']:
      self.assertIn(f'{key}={ip}', data)
    self.assertNotIn('127.0.0.1', data)

  def test_host_override(self):
    os.environ['UBIGS_HOST'] = '203.0.113.5'
    data = self.ini('127.0.0.1')
    self.assertIn('NATServerIP0=203.0.113.5', data)
    self.assertNotIn('127.0.0.1', data)

  def test_file_fallback(self):
    # no server socket (test client): the file as it is
    res = gsconnect.app.test_client().get('/gsinit.php?dp=HEROES_657d2c2ebadc6a1d')
    self.assertIn(b'RouterIP0=127.0.0.1', res.data)

if __name__ == '__main__':
  unittest.main()
