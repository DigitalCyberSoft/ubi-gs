import sys, os, socket, threading, unittest
# relative module import stuff
root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.append(root_dir)
sys.path.append(os.path.join(root_dir, 'homm5'))
import router, router_wm, proxy, proxy_wm, lobby
import gsm
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

  def test_ladder_query_closes_module(self):
    sock = start(proxy_wm)
    try:
      sock.sendall(game_message(gsm.MESSAGE_TYPE.PROXY_HANDLER, ['1281', '1', ['1', ['1', 'HEROES_657d2c2ebadc6a1d', '1', '0', ['1', ['alice', '1']], [[], [], []]]]]))
      self.assertEqual(sock.recv(64), b'')
    finally:
      sock.close()

if __name__ == '__main__':
  unittest.main()
