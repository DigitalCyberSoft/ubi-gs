import sys, os, tempfile, unittest
# relative module import stuff
root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.append(root_dir)
sys.path.append(os.path.join(root_dir, 'homm5'))
import accounts, client, gsm, ladder
from data import List
import proxy_wm

GAME = 'HEROES_657d2c2ebadc6a1d'

def module_request(code: str, seq: str, payload: list):
  header = gsm.GSMessageHeader.from_params(gsm.PROPERTY.GS, 1, gsm.MESSAGE_TYPE.PROXY_HANDLER, gsm.SENDER_RECEIVER.P, gsm.SENDER_RECEIVER.R)
  buf = bytes(gsm.GSMNotification(gsm.Message(None, header=header, dl=List([code, seq, payload]))))
  return gsm.Message(None, in_buf=buf)

def pivot_query(name: str):
  # as observed: ['1281','1',['1',['1','HEROES_…','1','0',['1',['alice','1']],[[],[],[]]]]]
  return ['1', ['1', GAME, '1', '0', ['1', [name, '1']], [[], [], []]]]

class LadderTests(unittest.TestCase):
  def setUp(self):
    self.dir = tempfile.TemporaryDirectory()
    accounts.configure(os.path.join(self.dir.name, 'ubigs.db'))
    self.clt = client.TcpClient((None, ('10.0.0.3', 50000)))

  def tearDown(self):
    accounts.configure(None)
    self.dir.cleanup()

  def query(self, name: str, seq: str = '1'):
    res = proxy_wm.handle_req(self.clt, module_request(ladder.QUERY, seq, pivot_query(name)))
    # round trip through the wire format
    return gsm.Message(None, in_buf=bytes(res))

  def test_defaults(self):
    accounts.login('alice', 'pw')
    msg = self.query('alice', '7')
    self.assertEqual(msg.header.type, gsm.MESSAGE_TYPE.PROXY_HANDLER)
    code, seq, (result, (version, (first, count, columns, rows))) = msg.dl.lst
    self.assertEqual((code, seq, result, version), ('1281', '7', '38', '1'))
    self.assertEqual(len(columns), 42)
    self.assertEqual(len(rows), 1)
    self.assertEqual(len(rows[0]), len(columns))
    values = dict(zip([c[0] for c in columns], rows[0]))
    self.assertEqual(values['RATING'], '1500')
    self.assertEqual(values['WINS'], '0')
    for value in rows[0]:
      self.assertTrue(value.isdigit())
    for name, name2 in columns:
      self.assertEqual(name, name2)
      self.assertLessEqual(len(name), 32)

  def test_stored_stats(self):
    alice = accounts.login('alice', 'pw')
    accounts.set_ladder_stats(alice, ladder.ladder_key(GAME, '1'), {'RATING': 1612, 'WINS': 3})
    values = dict(zip([c[0] for c in self.query('alice').dl.lst[2][1][1][2]], self.query('alice').dl.lst[2][1][1][3][0]))
    self.assertEqual((values['RATING'], values['WINS'], values['LOSSES']), ('1612', '3', '0'))

  def test_own_typed_name(self):
    # the game asks with the name typed at login
    accounts.login('alice', 'one')
    second = accounts.login('alice', 'two')
    accounts.set_ladder_stats(second, ladder.ladder_key(GAME, '1'), {'RATING': 1400})
    accounts.bind_session('alice', '10.0.0.3', second)
    values = self.query('alice').dl.lst[2][1][1][3][0]
    self.assertEqual(values[0], '1400')

  def test_unknown_player(self):
    self.assertEqual(self.query('nobody').dl.lst, ['1281', '1', ['39', ['0']]])

  def test_other_modules_close(self):
    with self.assertRaises(proxy_wm.ModuleUnavailable):
      proxy_wm.handle_req(self.clt, module_request('9999', '1', []))

if __name__ == '__main__':
  unittest.main()
