import sys, os, unittest
# relative module import stuff
root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.append(root_dir)
import client, gsm
from data import List

class StillAliveTests(unittest.TestCase):
  """Tests for `STILLALIVE` keepalive responses."""
  def test_still_alive_response(self):
    # keepalive sent by the game to the lobby server
    req = gsm.Message(None, in_buf=bytes([0x00, 0x00, 0x06, 0x01, 0x3A, 0x42]))
    res = gsm.StillAliveResponse(req)
    self.assertEqual(bytes(res), bytes([0x00, 0x00, 0x06, 0x01, 0x3A, 0x24]))

  def test_still_alive_response_router(self):
    req = gsm.Message(None, in_buf=bytes([0x00, 0x00, 0x06, 0x01, 0x3A, 0x41]))
    res = gsm.StillAliveResponse(req)
    self.assertEqual(res.header.type, gsm.MESSAGE_TYPE.STILLALIVE)
    self.assertEqual(res.header.sender, gsm.SENDER_RECEIVER.R)
    self.assertEqual(res.header.receiver, gsm.SENDER_RECEIVER.P)
    self.assertEqual(len(bytes(res)), gsm.GSMSG_HEADER_SIZE)

  def test_request_header_unchanged(self):
    req = gsm.Message(None, in_buf=bytes([0x00, 0x00, 0x06, 0x01, 0x3A, 0x42]))
    gsm.StillAliveResponse(req)
    res = gsm.StillAliveResponse(req)
    self.assertEqual(req.header.sender, gsm.SENDER_RECEIVER.P)
    self.assertEqual(req.header.receiver, gsm.SENDER_RECEIVER.S)
    self.assertEqual(res.header.sender, gsm.SENDER_RECEIVER.S)
    self.assertEqual(res.header.receiver, gsm.SENDER_RECEIVER.P)

  def test_still_alive_response_type(self):
    req = gsm.Message(None, in_buf=bytes([0x00, 0x00, 0x06, 0x01, 0x3B, 0x42]))
    with self.assertRaises(TypeError):
      gsm.StillAliveResponse(req)

class BundleTests(unittest.TestCase):
  """Packets with several messages."""
  def test_zero_size_message(self):
    # a second message claiming 0 bytes used to loop forever
    data = bytes([0, 0, 6, 0, 58, 0x24]) + bytes([0, 0, 0, 0, 58, 0x24])
    first = gsm.Message(None, in_buf=data)
    with self.assertRaises(BufferError):
      gsm.GSMessageBundle(first, data[first.header.size:], client.TcpClient((None, None)))

  def test_short_message(self):
    with self.assertRaises(BufferError):
      gsm.Message(None, in_buf=bytes([0, 0, 6]))

  def test_two_keepalives(self):
    data = bytes([0, 0, 6, 1, 58, 0x42]) * 2
    first = gsm.Message(None, in_buf=data)
    bundle = gsm.GSMessageBundle(first, data[first.header.size:], client.TcpClient((None, None)))
    self.assertEqual(len(bundle.msgs), 2)

class RedactionTests(unittest.TestCase):
  """Logs leave out credentials."""
  def test_login_password(self):
    header = gsm.GSMessageHeader.from_params(gsm.PROPERTY.GS, 1, gsm.MESSAGE_TYPE.LOGIN, gsm.SENDER_RECEIVER.P, gsm.SENDER_RECEIVER.R)
    buf = bytes(gsm.GSMNotification(gsm.Message(None, header=header, dl=List(['alice', 'secret', 'HEROES_c83663d1c69a2024', b'\x01']))))
    text = repr(gsm.Message(None, in_buf=buf))
    self.assertIn('alice', text)
    self.assertNotIn('secret', text)

class LengthCapTests(unittest.TestCase):
  """Strings relayed to the game fit its fixed buffers."""
  def test_playerinfo(self):
    header = gsm.GSMessageHeader.from_params(gsm.PROPERTY.GS, 1, gsm.MESSAGE_TYPE.PLAYERINFO, gsm.SENDER_RECEIVER.P, gsm.SENDER_RECEIVER.R)
    buf = bytes(gsm.GSMNotification(gsm.Message(None, header=header, dl=List(['averyveryverylongname']))))
    res = gsm.PlayerInfoResponse(gsm.Message(None, in_buf=buf), 'averyveryverylongname')
    fields = res.dl.lst[1]
    for field, size in zip(fields, gsm.PLAYERINFO_SIZES):
      self.assertLess(len(field.encode('utf8')), size)
    self.assertEqual(fields[0], 'averyveryve')

  def test_cap_character_boundary(self):
    import utils
    # 'é' is 2 bytes; never cut it in half
    self.assertEqual(utils.cap('ééééééé', 12), 'ééééé')
    self.assertEqual(utils.cap('short', 12), 'short')

if __name__ == '__main__':
  unittest.main()
