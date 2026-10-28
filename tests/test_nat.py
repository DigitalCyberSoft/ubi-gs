import sys, os, struct, unittest
from ctypes import c_uint32
# relative module import stuff
root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.append(root_dir)
import client, gsm, srp, utils
sys.path.append(os.path.join(root_dir, 'homm5'))
import gsnat
from data import List

CHECKSUM_INIT = 0x1AB7
"""Checksum initial value from the game's SYN window."""

SENDER_SIG = 0x0001
"""Sender signature from the game's SYN window."""

def make_checksum(data: bytes, init: int):
  """The game's segment checksum check (0x0047afe0), with `init` in the checksum field."""
  data = struct.pack('<H', init) + data[2:]
  # uint32 accumulator, an odd first byte is a signed char
  check_base = 0
  pos = 0
  if len(data) % 2 == 1:
    check_base = struct.unpack_from('<b', data, 0)[0] & 0xFFFFFFFF
    pos = 1
  for _ in range(len(data) >> 1):
    check_base = (check_base + struct.unpack_from('<H', data, pos)[0]) & 0xFFFFFFFF
    pos += 2
  folded = (check_base >> 16) + (check_base & 0xFFFF)
  return ~((folded >> 16) + folded) & 0xFFFF

def make_segment(flags: int, seg: int, ack: int, payload: bytes = b''):
  """Builds a game SRP segment."""
  header = struct.pack('<HHHHHH', 0, SENDER_SIG, len(payload), flags | srp.SRPHeaderFlags.SRP_ID.value, seg, ack)
  return header + payload

def make_nat_request(socket_id: str):
  """Builds the game's NAT address request."""
  header = gsm.GSMessageHeader.from_params(gsm.PROPERTY.GS, 1, gsm.MESSAGE_TYPE.NAT, gsm.SENDER_RECEIVER.UNK, gsm.SENDER_RECEIVER.S)
  dl = List(['1', [socket_id, '', 'HEROES_657d2c2ebadc6a1d', 'HEROES_c83663d1c69a2024']])
  return bytes(gsm.GSMNotification(gsm.Message(None, header=header, dl=dl)))

def make_client():
  """Returns a NAT client after SYN."""
  clt = client.NatClient(('127.0.0.1', 1024), None, None)
  window = struct.pack('<HHHH', 10, SENDER_SIG, CHECKSUM_INIT, 0x218)
  srp.SRPResponse(srp.SRPRequest(make_segment(srp.SRPHeaderFlags.SYN.value, 0, 0, window)), clt)
  return clt

class NatTests(unittest.TestCase):
  """Tests for NAT address requests and pings."""
  def test_ipv4_host_order(self):
    self.assertEqual(utils.ipv4_to_u32_be('127.0.0.1'), 0x7F000001)
    self.assertEqual(utils.ipv4_to_u32_be('192.168.0.41'), 0xC0A80029)

  def test_port_id(self):
    clt = make_client()
    req = srp.SRPRequest(make_segment(0, 1, 0, make_nat_request('1')))
    msg = gsm.NatResponse(req.segment.msg, gsm.NAT_MSG.PORT_ID, 5, 7781)
    res = srp.SRPResponse(req, clt, msg)
    self.assertEqual(res.msg.dl.lst, ['2', ['1', '5', '7781']])
    self.assertEqual(res.header.ack, 1)
    self.assertEqual(res.header.seg, 1)
    buf = bytes(res)
    self.assertEqual(res.header.checksum, make_checksum(buf, CHECKSUM_INIT))
    self.assertEqual(res.header.data_size, len(buf) - srp.SRP_HEADER_SIZE)

  def test_address_on_ping(self):
    clt = make_client()
    req = srp.SRPRequest(make_segment(0, 1, 0, make_nat_request('3')))
    srp.SRPResponse(req, clt, gsm.NatResponse(req.segment.msg, gsm.NAT_MSG.PORT_ID, 5, 7781))
    # ping from the game port, no client segment to answer
    msg = gsm.NatResponse(req.segment.msg, gsm.NAT_MSG.ADDRESS, utils.ipv4_to_u32_be('127.0.0.1'), 8888)
    res = srp.SRPResponse(None, clt, msg)
    self.assertEqual(res.msg.dl.lst, ['3', ['3', str(0x7F000001), '8888']])
    self.assertEqual(res.msg.header.sender, gsm.SENDER_RECEIVER.S)
    self.assertEqual(res.msg.header.receiver, gsm.SENDER_RECEIVER.UNK)
    self.assertEqual(res.header.flags, [srp.SRPHeaderFlags.ACK.name, srp.SRPHeaderFlags.SRP_ID.name])
    self.assertEqual(res.header.seg, 2)
    self.assertEqual(res.header.ack, 1)
    self.assertEqual(res.header.checksum, make_checksum(bytes(res), CHECKSUM_INIT))

  def test_checksum_odd_length(self):
    # odd-length segments with a checksum seed >= 0x80 in the first byte
    clt = make_client()
    req = srp.SRPRequest(make_segment(0, 1, 0, make_nat_request('1')))
    for ping_id in range(1, 40):
      msg = gsm.NatResponse(req.segment.msg, gsm.NAT_MSG.PORT_ID, ping_id * 0x1234567 & 0xFFFFFFFF, 7781)
      res = srp.SRPResponse(req, clt, msg)
      buf = bytes(res)
      self.assertEqual(res.header.checksum, make_checksum(buf, CHECKSUM_INIT), f'{len(buf)} bytes')

  def test_ping_id_round_trip(self):
    req = gsm.Message(None, in_buf=make_nat_request('1'))
    for ping_id in [1, 0x7FFFFFFF, 0x80000000, 0xDEADBEEF, 0xFFFFFFFF]:
      msg = gsm.NatResponse(req, gsm.NAT_MSG.PORT_ID, ping_id, 7781)
      # the game parses with atoi into an int and sends it back with htonl
      parsed = int(msg.dl.lst[1][1])
      self.assertTrue(-0x80000000 <= parsed <= 0x7FFFFFFF)
      ping = struct.pack('>i', parsed)
      self.assertEqual(utils.read_u32_be(ping), ping_id)

  def test_address_above_127(self):
    req = gsm.Message(None, in_buf=make_nat_request('1'))
    msg = gsm.NatResponse(req, gsm.NAT_MSG.ADDRESS, utils.ipv4_to_u32_be('192.168.0.41'), 8888)
    self.assertEqual(msg.dl.lst, ['3', ['1', str(0xC0A80029 - 0x100000000), '8888']])

  def test_find_by_ping(self):
    clt = make_client()
    clt.ping_id = 7
    self.assertIs(client.NatClient.find_by_ping(utils.read_u32_be(bytes([0, 0, 0, 7])), [clt]), clt)
    self.assertIsNone(client.NatClient.find_by_ping(8, [clt]))

class NatServerTests(unittest.TestCase):
  """Tests for the NAT service packet handling."""
  GAME = ('127.0.0.1', 1024)
  GAME_PORT = ('127.0.0.1', 8888)

  def setUp(self):
    gsnat.CLIENTS.clear()

  def connect(self, now: float):
    """SYN and NAT address request, returns the ping id."""
    window = struct.pack('<HHHH', 10, SENDER_SIG, CHECKSUM_INIT, 0x218)
    gsnat.handle_packet(make_segment(srp.SRPHeaderFlags.SYN.value, 0, 0, window), self.GAME, now)
    packets = gsnat.handle_packet(make_segment(0, 1, 0, make_nat_request('1')), self.GAME, now)
    res = srp.SRPRequest(packets[0][0])
    return c_uint32(int(res.segment.msg.dl.lst[1][1])).value

  def test_new_ping_id(self):
    ids = set()
    for _ in range(100):
      ping_id = gsnat.new_ping_id(gsnat.CLIENTS)
      self.assertTrue(0 < ping_id <= 0xFFFFFFFF)
      clt = client.NatClient(('127.0.0.1', 1024 + len(ids)), None, None)
      clt.ping_id = ping_id
      gsnat.CLIENTS.append(clt)
      ids.add(ping_id)
    self.assertEqual(len(ids), 100)

  def test_ping(self):
    ping_id = self.connect(0.0)
    packets = gsnat.handle_packet(struct.pack('>I', ping_id), self.GAME_PORT, 25.0)
    self.assertEqual(len(packets), 1)
    self.assertEqual(packets[0][1], self.GAME)
    res = srp.SRPRequest(packets[0][0])
    self.assertEqual(res.segment.msg.dl.lst, ['3', ['1', str(0x7F000001), '8888']])

  def test_fin_with_window_from_unknown_address(self):
    # found by fuzzing the Go port: no client, nothing to answer, no exception (#60)
    window = struct.pack('<HHHH', 10, SENDER_SIG, CHECKSUM_INIT, 0x218)
    fin = make_segment(srp.SRPHeaderFlags.FIN.value | srp.SRPHeaderFlags.SYN.value, 0, 0, window)
    self.assertEqual(gsnat.handle_packet(fin, self.GAME, 0.0), [])
    self.assertEqual(gsnat.handle_packet(make_segment(srp.SRPHeaderFlags.FIN.value, 1, 0, make_nat_request('1')), self.GAME, 0.0), [])
    self.assertEqual(gsnat.CLIENTS, [])

  def test_unknown_ping(self):
    ping_id = self.connect(0.0)
    self.assertEqual(gsnat.handle_packet(struct.pack('>I', ping_id ^ 1), self.GAME_PORT, 1.0), [])
    self.assertEqual(gsnat.handle_packet(b'\x00\x00', self.GAME_PORT, 1.0), [])

  def test_expire_silent_client(self):
    self.connect(0.0)
    gsnat.remove_silent(gsnat.CLIENT_TIMEOUT - 1)
    self.assertEqual(len(gsnat.CLIENTS), 1)
    gsnat.remove_silent(gsnat.CLIENT_TIMEOUT + 1)
    self.assertEqual(gsnat.CLIENTS, [])

  def test_ping_keeps_client(self):
    ping_id = self.connect(0.0)
    gsnat.handle_packet(struct.pack('>I', ping_id), self.GAME_PORT, 100.0)
    gsnat.remove_silent(gsnat.CLIENT_TIMEOUT + 1)
    self.assertEqual(len(gsnat.CLIENTS), 1)

  def test_reconnect_fresh_entry(self):
    old_id = self.connect(0.0)
    old = gsnat.CLIENTS[0]
    new_id = self.connect(10.0)
    self.assertEqual(len(gsnat.CLIENTS), 1)
    self.assertIsNot(gsnat.CLIENTS[0], old)
    # the restarted game's first segment is acknowledged from 0 again
    self.assertEqual(gsnat.CLIENTS[0].segment_id, 2)
    self.assertNotEqual(old_id, new_id)

if __name__ == '__main__':
  unittest.main()
