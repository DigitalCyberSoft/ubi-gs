import sys, os, socket, threading, time, unittest
# relative module import stuff
root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.append(root_dir)
sys.path.append(os.path.join(root_dir, 'homm5'))
import limits, router, gsnat, gsirc, ircm

STILLALIVE = bytes([0, 0, 6, 1, 58, 0x42])
SYN = bytes([0, 0, 1, 0, 8, 0, 0x42, 0x30, 0, 0, 0, 0, 10, 0, 1, 0, 0xB7, 0x1A, 0x18, 0x02])

def free_port():
  with socket.socket() as sock:
    sock.bind(('127.0.0.1', 0))
    return sock.getsockname()[1]

def closed(sock: socket.socket, wait: float = 2):
  """The server closed the connection within `wait` seconds."""
  sock.settimeout(wait)
  try:
    while sock.recv(64):
      pass
    return True
  except socket.timeout:
    return False
  except OSError:
    return True

def answers(sock: socket.socket):
  """The service answers a keepalive on the connection."""
  sock.settimeout(2)
  sock.sendall(STILLALIVE)
  try:
    return len(sock.recv(64)) > 0
  except OSError:
    return False

class LimitsTests(unittest.TestCase):
  def test_counts(self):
    lim = limits.ConnectionLimits(max_total=3, max_per_ip=2)
    self.assertTrue(lim.acquire('1.1.1.1'))
    self.assertTrue(lim.acquire('1.1.1.1'))
    self.assertFalse(lim.acquire('1.1.1.1'))
    self.assertTrue(lim.acquire('2.2.2.2'))
    self.assertFalse(lim.acquire('3.3.3.3'))
    lim.release('1.1.1.1')
    self.assertTrue(lim.acquire('3.3.3.3'))
    self.assertEqual(lim.per_ip, {'1.1.1.1': 1, '2.2.2.2': 1, '3.3.3.3': 1})

class TcpServiceTests(unittest.TestCase):
  """The router (all TCP services share the accept loop) within its limits."""
  @classmethod
  def setUpClass(cls):
    limits.TCP = limits.ConnectionLimits(max_total=10, max_per_ip=2, timeout=1)
    router.SERVER_ADDRESS = ('127.0.0.1', free_port())
    threading.Thread(target=router.start_server, daemon=True).start()
    time.sleep(0.3)

  def setUp(self):
    # let the server notice the previous test's closed connections
    time.sleep(0.3)

  def connect(self):
    return socket.create_connection(router.SERVER_ADDRESS, timeout=2)

  def test_per_ip_cap(self):
    a, b = self.connect(), self.connect()
    c = self.connect()
    self.assertTrue(closed(c))
    self.assertTrue(answers(a))
    a.close(), b.close()
    time.sleep(0.3)
    d = self.connect()
    self.assertTrue(answers(d))
    d.close()

  def test_silent_connection_closed(self):
    a = self.connect()
    self.assertTrue(closed(a, wait=3))
    # the slot is free again
    time.sleep(0.2)
    b = self.connect()
    self.assertTrue(answers(b))
    b.close()

  def test_bad_message_closes_only_its_connection(self):
    a = self.connect()
    a.sendall(bytes([0, 0, 6, 1, 0xFE, 0x42]))
    self.assertTrue(closed(a))
    b = self.connect()
    self.assertTrue(answers(b))
    b.close()

class NatTableTests(unittest.TestCase):
  def setUp(self):
    gsnat.CLIENTS.clear()

  def tearDown(self):
    gsnat.CLIENTS.clear()

  def test_per_ip_cap(self):
    for port in range(1000, 1000 + gsnat.MAX_CLIENTS_PER_IP + 50):
      gsnat.handle_packet(SYN, ('10.0.0.1', port), time.monotonic())
    self.assertEqual(len(gsnat.CLIENTS), gsnat.MAX_CLIENTS_PER_IP)
    self.assertEqual(len(gsnat.handle_packet(SYN, ('10.0.0.2', 1000), time.monotonic())), 1)

  def test_total_cap(self):
    cap, gsnat.MAX_CLIENTS = gsnat.MAX_CLIENTS, 20
    try:
      for i in range(100):
        gsnat.handle_packet(SYN, (f'10.1.{i}.1', 1000), time.monotonic())
      self.assertEqual(len(gsnat.CLIENTS), 20)
      self.assertEqual(gsnat.handle_packet(SYN, ('10.2.0.1', 1000), time.monotonic()), [])
    finally:
      gsnat.MAX_CLIENTS = cap

class IrcLimitsTests(unittest.TestCase):
  def setUp(self):
    self.server = gsirc.IRCServer(('127.0.0.1', 0), gsirc.IRCClient)
    self.server.limits = limits.ConnectionLimits(max_total=10, max_per_ip=2, timeout=1.5)
    threading.Thread(target=self.server.serve_forever, daemon=True).start()

  def tearDown(self):
    self.server.shutdown()
    self.server.server_close()

  def connect(self):
    return socket.create_connection(self.server.server_address, timeout=2)

  def test_per_ip_cap(self):
    a, b, c = self.connect(), self.connect(), self.connect()
    self.assertTrue(closed(c))
    self.assertFalse(closed(a, wait=0.3))
    a.close(), b.close()

  def test_ping_keeps_a_quiet_client(self):
    a = self.connect()
    a.settimeout(2)
    data = a.recv(1024)
    self.assertIn(b'PING', ircm.IRCMessage(data).payload)
    a.sendall(bytes(ircm.IRCMessage.from_str('PONG :localhost\r\n')))
    # silent clients go after 1.5s; this one answered, so it is still served after that
    time.sleep(1)
    a.sendall(bytes(ircm.IRCMessage.from_str('PING :check\r\n')))
    replies = b''
    try:
      while b'PONG' not in replies:
        data = a.recv(1024)
        if not data:
          break
        replies += ircm.IRCMessage(data[:2 + int.from_bytes(data[:2], 'big')]).payload
    except OSError:
      pass
    self.assertIn(b'PONG', replies)
    a.close()

  def test_silent_client_dropped(self):
    a = self.connect()
    self.assertTrue(closed(a, wait=3))

if __name__ == '__main__':
  unittest.main()
