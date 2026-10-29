import sys, os, socket, threading, unittest
# relative module import stuff
# accounts in memory, no database file from the tests
os.environ.setdefault('UBIGS_DB', ':memory:')
root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.append(root_dir)
sys.path.append(os.path.join(root_dir, 'homm5'))
import router, router_wm, proxy, proxy_wm, lobby
import gsm, pkc
from data import List

STILLALIVE = bytes([0x00, 0x00, 0x06, 0x01, 0x3A, 0x42])
"""Keepalive sent by the game."""

def free_port():
  with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
    sock.bind(('127.0.0.1', 0))
    return sock.getsockname()[1]

class ConcurrentClientsTests(unittest.TestCase):
  """Every TCP service serves several game clients at once."""
  def check_service(self, service):
    service.SERVER_ADDRESS = ('127.0.0.1', free_port())
    threading.Thread(target=service.start_server, daemon=True).start()
    first = second = None
    for _ in range(50):
      try:
        first = socket.create_connection(service.SERVER_ADDRESS, timeout=2)
        break
      except ConnectionRefusedError:
        threading.Event().wait(0.05)
    try:
      # the first client stays connected and silent
      second = socket.create_connection(service.SERVER_ADDRESS, timeout=2)
      second.sendall(STILLALIVE)
      res = second.recv(64)
      self.assertEqual(res[4], 0x3A)
      first.sendall(STILLALIVE)
      self.assertEqual(first.recv(64)[4], 0x3A)
    finally:
      for sock in [first, second]:
        if sock is not None:
          sock.close()

  def test_router(self):
    self.check_service(router)

  def test_router_wm(self):
    self.check_service(router_wm)

  def test_proxy(self):
    self.check_service(proxy)

  def test_proxy_wm(self):
    self.check_service(proxy_wm)

  def test_lobby(self):
    self.check_service(lobby)

def game_message(msg_type: gsm.MESSAGE_TYPE, data: list):
  """Builds a game request."""
  header = gsm.GSMessageHeader.from_params(gsm.PROPERTY.GS, 1, msg_type, gsm.SENDER_RECEIVER.P, gsm.SENDER_RECEIVER.R)
  return bytes(gsm.GSMNotification(gsm.Message(None, header=header, dl=List(data))))

def start(service):
  service.SERVER_ADDRESS = ('127.0.0.1', free_port())
  threading.Thread(target=service.start_server, daemon=True).start()
  for _ in range(50):
    try:
      return socket.create_connection(service.SERVER_ADDRESS, timeout=2)
    except ConnectionRefusedError:
      threading.Event().wait(0.05)

class LobbyRobustnessTests(unittest.TestCase):
  def test_unknown_request_keeps_player(self):
    sock = start(lobby)
    try:
      # a lobby message the server doesn't know (subtype 99) must not close the connection
      sock.sendall(game_message(gsm.MESSAGE_TYPE.LOBBY_MSG, ['99', ['1']]))
      sock.sendall(STILLALIVE)
      self.assertEqual(sock.recv(64)[4], 0x3A)
    finally:
      sock.close()

class FramingTests(unittest.TestCase):
  """Messages split across reads, several in one read, and messages over 4 KB."""
  def test_split_message(self):
    for service in [router, lobby]:
      sock = start(service)
      try:
        sock.sendall(STILLALIVE[:2])
        threading.Event().wait(0.2)
        sock.sendall(STILLALIVE[2:])
        self.assertEqual(sock.recv(64)[4], 0x3A)
      finally:
        sock.close()

  def test_coalesced_messages(self):
    # the router used to read only the first message of a recv()
    for service in [router, proxy]:
      sock = start(service)
      try:
        sock.sendall(STILLALIVE * 3)
        data = b''
        while len(data) < 18:
          data += sock.recv(64)
        self.assertEqual(data, bytes([0, 0, 6, 1, 0x3A, 0x24]) * 3)
      finally:
        sock.close()

  def test_large_profile(self):
    import accounts
    alice = accounts.login('framing', 'pw')
    accounts.bind_session('framing', '127.0.0.1', alice)
    sock = start(proxy_wm)
    try:
      # read in several pieces, and answered: refused, as it is no profile
      blob = bytes(range(256)) * 40
      payload = ['HEROES_657d2c2ebadc6a1d', '0', 'framing', '0', 'PUBLIC', blob, str(len(blob))]
      sock.sendall(game_message(gsm.MESSAGE_TYPE.PROXY_HANDLER, ['1026', '7', payload]))
      res = sock.recv(4096)
      self.assertEqual(gsm.Message(None, in_buf=res).dl.lst, ['1026', '7', ['39', ['0']]])
      self.assertIsNone(accounts.profile(alice, 'HEROES_657d2c2ebadc6a1d'))
    finally:
      sock.close()

class ProxyModuleTests(unittest.TestCase):
  """Proxy module handshakes."""
  def test_module_ready_not_echoed(self):
    sock = start(router_wm)
    try:
      # module 1 is ready; an echo would block the game's proxy messages
      sock.sendall(game_message(gsm.MESSAGE_TYPE.PROXY_HANDLER, [['3', '1']]))
      sock.sendall(STILLALIVE)
      self.assertEqual(sock.recv(64), bytes([0x00, 0x00, 0x06, 0x01, 0x3A, 0x24]))
    finally:
      sock.close()

  def test_unknown_module_data_closes_module(self):
    sock = start(proxy_wm)
    try:
      sock.sendall(game_message(gsm.MESSAGE_TYPE.PROXY_HANDLER, ['9999', '1', []]))
      self.assertEqual(sock.recv(64), b'')
    finally:
      sock.close()

class KeyExchangeTests(unittest.TestCase):
  def test_one_server_key(self):
    # every exchange answers with the process's key (a key per exchange took about 250 ms of CPU, for anyone
    # who asked), and each still ends with the game holding its session's Blowfish key
    game_pub, game_priv = pkc.keygen()
    buf = bytes(pkc.RsaPublicKey.from_pubkey(game_pub))
    server_keys = []
    start(router).close()
    for _ in range(2):
      sock = socket.create_connection(router.SERVER_ADDRESS, timeout=2)
      try:
        sock.sendall(game_message(gsm.MESSAGE_TYPE.KEY_EXCHANGE, ['1', ['1', str(len(buf)), buf]]))
        server_key = bytes(gsm.Message(None, in_buf=sock.recv(4096)).dl.lst[1][2])
        server_keys.append(server_key)
        sv_pub = pkc.RsaPublicKey.from_buf(server_key).to_pubkey()
        enc = pkc.encrypt(os.urandom(16), sv_pub)
        sock.sendall(game_message(gsm.MESSAGE_TYPE.KEY_EXCHANGE, ['2', ['1', str(len(enc)), enc]]))
        enc_key = bytes(gsm.Message(None, in_buf=sock.recv(4096)).dl.lst[1][2])
        self.assertEqual(len(pkc.decrypt(enc_key, game_priv)), 16)
      finally:
        sock.close()
    self.assertEqual(server_keys[0], server_keys[1])

if __name__ == '__main__':
  unittest.main()
