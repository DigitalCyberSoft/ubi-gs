import sys, os, tempfile, unittest
# relative module import stuff
root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.append(root_dir)
sys.path.append(os.path.join(root_dir, 'homm5'))
import accounts, client, gsm, profiles
from data import List
import proxy_wm

GAME = 'HEROES_657d2c2ebadc6a1d'

def module_request(code: str, seq: str, payload: list):
  header = gsm.GSMessageHeader.from_params(gsm.PROPERTY.GS, 1, gsm.MESSAGE_TYPE.PROXY_HANDLER, gsm.SENDER_RECEIVER.P, gsm.SENDER_RECEIVER.R)
  buf = bytes(gsm.GSMNotification(gsm.Message(None, header=header, dl=List([code, seq, payload]))))
  return gsm.Message(None, in_buf=buf)

class ProfileTests(unittest.TestCase):
  def setUp(self):
    self.dir = tempfile.TemporaryDirectory()
    self.path = os.path.join(self.dir.name, 'ubigs.db')
    accounts.configure(self.path)
    self.alice = accounts.login('alice', 'pw')
    accounts.bind_session('alice', '10.0.0.2', self.alice)
    self.clt = client.TcpClient((None, ('10.0.0.2', 50000)))

  def tearDown(self):
    accounts.configure(None)
    self.dir.cleanup()

  def request(self, code, seq, payload):
    res = proxy_wm.handle_req(self.clt, module_request(code, seq, payload))
    return gsm.Message(None, in_buf=bytes(res)).dl.lst

  def test_nothing_stored(self):
    self.assertEqual(self.request(profiles.GET, '1', [GAME, '0', 'alice', '0', 'PUBLIC']), ['1025', '1', ['39', ['1']]])

  def test_set_then_get(self):
    blob = bytes(range(256)) * 3
    self.assertEqual(self.request(profiles.SET, '2', [GAME, '0', 'alice', '0', 'PUBLIC', blob, str(len(blob))]), ['1026', '2', ['38', []]])
    self.assertEqual(self.request(profiles.GET, '3', [GAME, '0', 'alice', '0', 'PUBLIC']), ['1025', '3', ['38', [blob, str(len(blob))]]])
    # kept across a restart, and readable by others under the account name
    accounts.configure(self.path)
    other = client.TcpClient((None, ('10.0.0.9', 50000)))
    res = proxy_wm.handle_req(other, module_request(profiles.GET, '1', [GAME, '0', 'ALICE', '0', 'PUBLIC']))
    self.assertEqual(gsm.Message(None, in_buf=bytes(res)).dl.lst[2], ['38', [blob, str(len(blob))]])

  def test_only_own_data(self):
    # a game without a router login for that name can't write it
    other = client.TcpClient((None, ('10.0.0.9', 50000)))
    res = proxy_wm.handle_req(other, module_request(profiles.SET, '1', [GAME, '0', 'alice', '0', 'PUBLIC', b'x', '1']))
    self.assertEqual(gsm.Message(None, in_buf=bytes(res)).dl.lst[2][0], '39')
    self.assertIsNone(accounts.profile(self.alice, GAME))

  def test_size_limit(self):
    blob = b'x' * (profiles.MAX_SIZE + 1)
    self.assertEqual(self.request(profiles.SET, '1', [GAME, '0', 'alice', '0', 'PUBLIC', blob, str(len(blob))])[2][0], '39')

if __name__ == '__main__':
  unittest.main()
