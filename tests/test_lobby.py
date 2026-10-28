import sys, os, unittest
# relative module import stuff
# accounts in memory, no database file from the tests
os.environ.setdefault('UBIGS_DB', ':memory:')
root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.append(root_dir)
sys.path.append(os.path.join(root_dir, 'homm5'))
import client, gsm, lobby, h5_data
from data import List

ROOM_INFO = bytes.fromhex(
  "04080400000001d90200000208ffffffff0308ffffffff04240220000000000000000000000000000000000602000730"
  "61006c00690063006500270073002000470061006d00650008000902000a02000b02000c02000d08ffffffff0e080100"
  "00000f6602622f4d6170732f4d756c7469706c617965722f53342f53342e7864622378706f696e746572282f4164764d"
  "61704465736329132001080200000002100100000001000000140802000000150801000000161803080000000004080d"
  "00000017083c0001001802001920000000000000000000000000000000001a02001b5403080100000004080d00000001"
  "206175746f736176655f656e61626c65640214020800000000030430001c02001d087737aa291e08010000001f760272"
  "2f4d6170732f4d756c7469706c617965722f53342f6d61702d7461672e7864622378706f696e746572282f4164764d61"
  "704465736354616729200a676f616c352100250800000000260800000000270800000000280200000002000500"
)
"""Room info sent by the game with `CREATE_ROOM` ('alice's Game', 2 players)."""

class FakeConn:
  """Socket stand-in collecting what the server sends."""
  def __init__(self):
    self.sent: list[bytes] = []

  def sendall(self, data: bytes):
    self.sent.append(data)

def make_client(name: str):
  clt = client.TcpClient((FakeConn(), ('127.0.0.1', 50000 + len(lobby.g_clients))))
  lobby.g_clients.append(clt)
  return clt

def request(clt: client.TcpClient, msg_type: gsm.MESSAGE_TYPE, data: list):
  """Builds a game request and lets the lobby handle it, returns the response."""
  header = gsm.GSMessageHeader.from_params(gsm.PROPERTY.GS, 1, msg_type, gsm.SENDER_RECEIVER.P, gsm.SENDER_RECEIVER.S)
  buf = bytes(gsm.GSMNotification(gsm.Message(None, header=header, dl=List(data))))
  return lobby.handle_req(clt, gsm.Message(None, in_buf=buf))

def notifs(clt: client.TcpClient):
  """Decodes and clears the `LOBBY_MSG` notifications sent to the client."""
  msgs = [gsm.Message(None, in_buf=buf).dl.lst for buf in clt.conn.sent]
  clt.conn.sent.clear()
  return msgs

def login(name: str):
  clt = make_client(name)
  request(clt, gsm.MESSAGE_TYPE.LOBBYSERVERLOGIN, [name, '1', '192.168.0.41', '255.255.255.0', '0'])
  request(clt, gsm.MESSAGE_TYPE.LOBBY_MSG, ['23', ['1', '', '384']])
  request(clt, gsm.MESSAGE_TYPE.LOBBY_MSG, ['9', ['1', '384']])
  return clt

class LobbyTests(unittest.TestCase):
  """Tests for the lobby room list, members and notifications."""
  def setUp(self):
    lobby.g_clients.clear()
    lobby.g_rooms.clear()

  def create_room(self, host: client.TcpClient):
    res = request(host, gsm.MESSAGE_TYPE.LOBBY_MSG, ['12', ['1', "alice's Game", 'HEROES_657d2c2ebadc6a1d', '7', '2', '0', ROOM_INFO, '', '', 'HEROES_c83663d1c69a2024', b'']])
    room_id = int(res.dl.lst[1][1][0])
    request(host, gsm.MESSAGE_TYPE.LOBBY_MSG, ['24', [str(room_id), '', '448', '0', 'HEROES_c83663d1c69a2024']])
    return room_id

  def test_members(self):
    alice = login('alice')
    msgs = notifs(alice)
    self.assertEqual(msgs[0][0], '53')
    self.assertEqual([m[0] for m in msgs[0][1][4]], ['alice'])
    bob = login('bob')
    # bob gets everybody, alice gets bob
    self.assertEqual([m[0] for m in notifs(bob)[0][1][4]], ['alice', 'bob'])
    self.assertEqual(notifs(alice), [['50', ['bob', '0', '1', '127.0.0.1', '192.168.0.41', b'', '0']]])

  def test_room_list(self):
    alice = login('alice')
    bob = login('bob')
    notifs(alice)
    notifs(bob)
    room_id = self.create_room(alice)
    new_group = [m for m in notifs(bob) if m[0] == '54']
    self.assertEqual(len(new_group), 1)
    # room data: type, name, id, lobby server, parent, ... game title
    self.assertEqual(new_group[0][1][:3], ['7', "alice's Game", str(room_id)])
    self.assertEqual(new_group[0][1][9], 'HEROES_657d2c2ebadc6a1d')
    # a player joining later gets the room with the lobby
    carol = login('carol')
    rooms = notifs(carol)[0][1][3]
    self.assertEqual([r[2] for r in rooms], [str(room_id)])

  def test_join_and_leave_room(self):
    alice = login('alice')
    bob = login('bob')
    room_id = self.create_room(alice)
    notifs(alice)
    notifs(bob)
    request(bob, gsm.MESSAGE_TYPE.LOBBY_MSG, ['24', [str(room_id), '', '448', '0', 'HEROES_c83663d1c69a2024']])
    info = [m for m in notifs(bob) if m[0] == '53'][0]
    self.assertEqual([m[0] for m in info[1][4]], ['alice', 'bob'])
    self.assertIn(['50', ['bob', '0', str(room_id), '127.0.0.1', '192.168.0.41', b'', '2']], notifs(alice))
    self.assertEqual(lobby.find_room(room_id).gs_room.nb_players, 2)
    res = request(bob, gsm.MESSAGE_TYPE.LOBBY_MSG, ['8', [str(room_id)]])
    self.assertEqual(res.dl.lst, ['38', ['8', [str(room_id)]]])
    self.assertIn(['51', ['bob', str(room_id)]], notifs(alice))
    self.assertEqual(lobby.find_room(room_id).gs_room.nb_players, 1)

  def test_host_leaves(self):
    alice = login('alice')
    bob = login('bob')
    room_id = self.create_room(alice)
    notifs(bob)
    lobby.g_clients.remove(alice)
    lobby.leave_lobby(alice)
    self.assertEqual(notifs(bob), [['55', [str(room_id)]], ['51', ['alice', '1']]])
    self.assertIsNone(lobby.find_room(room_id))

  def test_room_update(self):
    alice = login('alice')
    bob = login('bob')
    room_id = self.create_room(alice)
    notifs(alice)
    notifs(bob)
    request(alice, gsm.MESSAGE_TYPE.LOBBY_MSG, ['31', [str(room_id), '64', ROOM_INFO]])
    # room data only, members come with MEMBER_JOIN
    for clt in [alice, bob]:
      msgs = notifs(clt)
      self.assertEqual(len(msgs), 1)
      self.assertEqual(msgs[0][1][:2], [str(room_id), '64'])
      self.assertEqual(msgs[0][1][3:], [[], []])

  def test_set_player_info(self):
    alice = login('alice')
    bob = login('bob')
    notifs(alice)
    res = request(bob, gsm.MESSAGE_TYPE.LOBBY_MSG, ['42', [b'data']])
    self.assertEqual(res.dl.lst, ['38', ['42']])
    self.assertEqual(notifs(alice), [['66', ['bob', b'data']]])
    carol = login('carol')
    members = notifs(carol)[0][1][4]
    self.assertEqual(members[1][4], b'data')

  def test_member_data_from_room_info(self):
    alice = login('alice')
    bob = login('bob')
    room_id = self.create_room(alice)
    # the host adds itself to the room info with GROUP_CONFIG_UPDATE_RES
    entry = h5_data.H5_PlayerInfo()
    entry.username = 'alice'
    entry.ext_ip.family = 2
    entry.ext_ip.port = 8888
    entry.ext_ip.ip = '127.0.0.1'
    entry.local_port = 8888
    lobby.find_room(room_id).room_info.player_infos.append(entry)
    notifs(bob)
    request(bob, gsm.MESSAGE_TYPE.LOBBY_MSG, ['24', [str(room_id), '', '448', '0', 'HEROES_c83663d1c69a2024']])
    members = [m for m in notifs(bob) if m[0] == '53'][0][1][4]
    # alice's entry of the room info, in the game's PlayerInfo stream format
    data = members[0][4]
    self.assertEqual(data, h5_data.H5_Serializer().serialize_playerinfo(entry))
    self.assertIn(b'\x0aalice', data)
    # bob is not in the room info yet
    self.assertEqual(members[1][4], b'')

if __name__ == '__main__':
  unittest.main()
