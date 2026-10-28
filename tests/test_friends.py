import sys, os, tempfile, unittest
# relative module import stuff
root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.append(root_dir)
sys.path.append(os.path.join(root_dir, 'homm5'))
import accounts, client, gsm
from data import List
import router_wm

class FakeConn:
  def __init__(self):
    self.sent = []
  def sendall(self, data):
    self.sent.append(data)

def message(msg_type: gsm.MESSAGE_TYPE, data: list):
  header = gsm.GSMessageHeader.from_params(gsm.PROPERTY.GS, 1, msg_type, gsm.SENDER_RECEIVER.P, gsm.SENDER_RECEIVER.R)
  buf = bytes(gsm.GSMNotification(gsm.Message(None, header=header, dl=List(data))))
  return gsm.Message(None, in_buf=buf)

def sent(clt: client.TcpClient):
  """Decodes and clears what was sent to the client: (type, data)."""
  msgs = [gsm.Message(None, in_buf=buf) for buf in clt.conn.sent]
  clt.conn.sent.clear()
  return [(m.header.type, m.dl.lst) for m in msgs]

ONLINE = (1).to_bytes(4, 'little')
OFFLINE = (0).to_bytes(4, 'little')

class FriendsTests(unittest.TestCase):
  def setUp(self):
    self.dir = tempfile.TemporaryDirectory()
    self.path = os.path.join(self.dir.name, 'ubigs.db')
    accounts.configure(self.path)
    router_wm.CLIENTS.clear()

  def tearDown(self):
    router_wm.CLIENTS.clear()
    accounts.configure(None)
    self.dir.cleanup()

  def connect(self, name: str, ip: str, password: str = 'pw'):
    """A player logged in at the router, connected to the wait module."""
    accounts.bind_session(name, ip, accounts.login(name, password))
    clt = client.TcpClient((FakeConn(), (ip, 50000)))
    router_wm.CLIENTS.append(clt)
    self.request(clt, gsm.MESSAGE_TYPE.LOGINWAITMODULE, [name])
    self.request(clt, gsm.MESSAGE_TYPE.LOGINFRIENDS, [ONLINE, OFFLINE])
    return clt

  def request(self, clt, msg_type, data):
    res = router_wm.handle_req(clt, message(msg_type, data))
    if res:
      router_wm.send_response(clt, res)
    return res

  def test_add_list_remove(self):
    alice = self.connect('alice', '10.0.0.2')
    self.connect('bob', '10.0.0.3')
    sent(alice)
    self.request(alice, gsm.MESSAGE_TYPE.ADDFRIEND, ['BOB', '', OFFLINE])
    self.assertEqual(sent(alice), [
      (gsm.MESSAGE_TYPE.GSSUCCESS, [b'K', 'bob']),
      (gsm.MESSAGE_TYPE.UPDATEFRIEND, ['bob', ONLINE, '', OFFLINE, OFFLINE, ''])])
    self.request(alice, gsm.MESSAGE_TYPE.FRIENDLIST, [])
    self.assertEqual(sent(alice), [
      (gsm.MESSAGE_TYPE.GSSUCCESS, [b'Y']),
      (gsm.MESSAGE_TYPE.UPDATEFRIEND, ['bob', ONLINE, '', OFFLINE, OFFLINE, ''])])
    self.request(alice, gsm.MESSAGE_TYPE.DELFRIEND, ['bob'])
    self.assertEqual(sent(alice), [(gsm.MESSAGE_TYPE.GSSUCCESS, [b'L', 'bob'])])
    self.request(alice, gsm.MESSAGE_TYPE.FRIENDLIST, [])
    self.assertEqual(sent(alice), [(gsm.MESSAGE_TYPE.GSSUCCESS, [b'Y'])])

  def test_unknown_player(self):
    alice = self.connect('alice', '10.0.0.2')
    sent(alice)
    self.request(alice, gsm.MESSAGE_TYPE.ADDFRIEND, ['nobody', '', OFFLINE])
    self.assertEqual(sent(alice), [(gsm.MESSAGE_TYPE.GSFAIL, [b'K', [(1).to_bytes(4, 'little')]])])
    self.request(alice, gsm.MESSAGE_TYPE.ADDFRIEND, ['alice', '', OFFLINE])
    self.assertEqual(sent(alice)[0][0], gsm.MESSAGE_TYPE.GSFAIL)

  def test_online_and_offline(self):
    alice = self.connect('alice', '10.0.0.2')
    accounts.login('bob', 'pw')
    self.request(alice, gsm.MESSAGE_TYPE.ADDFRIEND, ['bob', '', OFFLINE])
    sent(alice)
    # bob logs in: alice is told
    bob = self.connect('bob', '10.0.0.3')
    self.assertIn((gsm.MESSAGE_TYPE.UPDATEFRIEND, ['bob', ONLINE, '', OFFLINE, OFFLINE, '']), sent(alice))
    # and logs out
    router_wm.CLIENTS.remove(bob)
    router_wm.notify_friend_of(bob, False)
    self.assertEqual(sent(alice), [(gsm.MESSAGE_TYPE.UPDATEFRIEND, ['bob', OFFLINE, '', OFFLINE, OFFLINE, ''])])

  def test_list_at_login_and_persistence(self):
    alice = self.connect('alice', '10.0.0.2')
    accounts.login('bob', 'pw')
    self.request(alice, gsm.MESSAGE_TYPE.ADDFRIEND, ['bob', '', OFFLINE])
    router_wm.CLIENTS.clear()
    # a server restart
    accounts.configure(self.path)
    alice = self.connect('alice', '10.0.0.2')
    self.assertIn((gsm.MESSAGE_TYPE.UPDATEFRIEND, ['bob', OFFLINE, '', OFFLINE, OFFLINE, '']), sent(alice))

if __name__ == '__main__':
  unittest.main()
