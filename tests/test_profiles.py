import sys, os, struct, tempfile, unittest
# relative module import stuff
root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.append(root_dir)
sys.path.append(os.path.join(root_dir, 'homm5'))
import accounts, client, gsm, profiles
from data import List
import proxy_wm

GAME = 'HEROES_657d2c2ebadc6a1d'

# what the game sent after "hello from ubigs" was typed into a new profile's Additional Information
SAVED = bytes.fromhex('0408040000000154015002083c0001000440680065006c006c006f002000660072006f006d002000750062006900670073'
  '00000002000500')

def chunk(chunk_id: int, data: bytes, long: bool = False):
  """A chunk or section: [id][length * 2], or [id][length * 2 | 1 as 4 bytes], then the data."""
  n = len(data) * 2
  return bytes([chunk_id]) + (struct.pack('<I', n | 1) if long or n >= 0x100 else bytes([n])) + data

VERSION = chunk(4, struct.pack('<i', 4))
EMPTY = chunk(0, b'') + chunk(2, b'') + chunk(5, b'')

def profile(*fields: bytes, sections: bytes = EMPTY):
  return VERSION + chunk(1, chunk(1, b''.join(fields))) + sections

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
    # a new player gets the default profile, as the game itself would write it
    self.assertEqual(profiles.DEFAULT, bytes.fromhex('0408040000000114011002083c0001000400000002000500'))
    self.assertEqual(self.request(profiles.GET, '1', [GAME, '0', 'alice', '0', 'PUBLIC']),
      ['1025', '1', ['38', [profiles.DEFAULT, str(len(profiles.DEFAULT))]]])

  def test_no_such_player(self):
    self.assertEqual(self.request(profiles.GET, '1', [GAME, '0', 'nobody', '0', 'PUBLIC']), ['1025', '1', ['39', ['1']]])

  def test_codec(self):
    self.assertEqual(profiles.decode(SAVED), (0x1003c, 'hello from ubigs'))
    self.assertEqual(profiles.encode(0x1003c, 'hello from ubigs'), SAVED)
    self.assertEqual(profiles.decode(profiles.DEFAULT), (profiles.DEFAULT_AVATAR, ''))
    # the longest text takes 4-byte lengths
    long = profiles.encode(7, 'é' * profiles.MAX_TEXT)
    self.assertLessEqual(len(long), profiles.MAX_SIZE)
    self.assertEqual(profiles.decode(long), (7, 'é' * profiles.MAX_TEXT))
    # missing fields read as the defaults, as in the game
    self.assertEqual(profiles.decode(profile()), (profiles.DEFAULT_AVATAR, ''))
    self.assertEqual(profiles.decode(profile(chunk(4, 'hi'.encode('utf-16-le')))), (profiles.DEFAULT_AVATAR, 'hi'))

  def test_set_then_get(self):
    self.assertEqual(self.request(profiles.SET, '2', [GAME, '0', 'alice', '0', 'PUBLIC', SAVED, str(len(SAVED))]), ['1026', '2', ['38', []]])
    self.assertEqual(self.request(profiles.GET, '3', [GAME, '0', 'alice', '0', 'PUBLIC']), ['1025', '3', ['38', [SAVED, str(len(SAVED))]]])
    # kept across a restart, and readable by others under the account name
    accounts.configure(self.path)
    other = client.TcpClient((None, ('10.0.0.9', 50000)))
    res = proxy_wm.handle_req(other, module_request(profiles.GET, '1', [GAME, '0', 'ALICE', '0', 'PUBLIC']))
    self.assertEqual(gsm.Message(None, in_buf=bytes(res)).dl.lst[2], ['38', [SAVED, str(len(SAVED))]])

  def test_stored_as_written_again(self):
    # the same profile with 4-byte lengths everywhere and without the empty sections
    text = 'hello from ubigs'.encode('utf-16-le')
    loose = chunk(4, struct.pack('<i', 4), True) + chunk(1, chunk(1, chunk(2, struct.pack('<i', 0x1003c), True) +
      chunk(4, text, True), True), True)
    self.assertEqual(profiles.decode(loose), (0x1003c, 'hello from ubigs'))
    self.assertEqual(self.request(profiles.SET, '1', [GAME, '0', 'alice', '0', 'PUBLIC', loose, str(len(loose))])[2], ['38', []])
    self.assertEqual(accounts.profile(self.alice, GAME), SAVED)

  def test_only_profiles(self):
    avatar = chunk(2, struct.pack('<i', 1))
    bad = [
      b'x',
      b'',
      SAVED[:-1],                                                      # truncated
      chunk(4, struct.pack('<i', 5)) + SAVED[6:],                      # another version
      profile(avatar, sections=EMPTY[:-2] + chunk(5, b'x')),            # a type map entry
      profile(avatar, sections=chunk(0, b'\0' * 9) + EMPTY[2:]),        # an object
      SAVED + chunk(3, b''),                                           # an unknown section
      SAVED + chunk(4, struct.pack('<i', 4)),                          # a section twice
      VERSION + chunk(1, chunk(1, b'') + chunk(1, b'')),               # a profile twice
      VERSION + chunk(1, chunk(3, b'')),                               # something else
      profile(avatar, chunk(4, b'abc')),                               # odd text size
      profiles.encode(1, 'x' * (profiles.MAX_TEXT + 1)),               # too long a text
      profile(chunk(2, b'\1\0')),                                       # a short avatar
      profile(avatar, chunk(6, b'')),                                  # an unknown field
      profile(avatar, avatar),                                         # a field twice
    ]
    for data in bad:
      with self.subTest(data=data.hex()):
        self.assertRaises(ValueError, profiles.decode, data)
        self.assertEqual(self.request(profiles.SET, '1', [GAME, '0', 'alice', '0', 'PUBLIC', data, str(len(data))])[2], ['39', ['0']])
    self.assertIsNone(accounts.profile(self.alice, GAME))

  def test_only_own_data(self):
    # a game without a router login for that name can't write it
    other = client.TcpClient((None, ('10.0.0.9', 50000)))
    res = proxy_wm.handle_req(other, module_request(profiles.SET, '1', [GAME, '0', 'alice', '0', 'PUBLIC', SAVED, str(len(SAVED))]))
    self.assertEqual(gsm.Message(None, in_buf=bytes(res)).dl.lst[2][0], '39')
    self.assertIsNone(accounts.profile(self.alice, GAME))

  def test_size_limit(self):
    blob = SAVED + b'x' * (profiles.MAX_SIZE + 1 - len(SAVED))
    self.assertEqual(self.request(profiles.SET, '1', [GAME, '0', 'alice', '0', 'PUBLIC', blob, str(len(blob))])[2][0], '39')

if __name__ == '__main__':
  unittest.main()
