import sys, os, unittest
# relative module import stuff
root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.append(root_dir)
import gsm

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

  def test_still_alive_response_type(self):
    req = gsm.Message(None, in_buf=bytes([0x00, 0x00, 0x06, 0x01, 0x3B, 0x42]))
    with self.assertRaises(TypeError):
      gsm.StillAliveResponse(req)

if __name__ == '__main__':
  unittest.main()
