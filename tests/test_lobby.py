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
  res = lobby.handle_req(clt, gsm.Message(None, in_buf=buf))
  if res and res is not lobby.NO_REPLY:
    lobby.send_response(clt, res)
  return res

def notifs(clt: client.TcpClient):
  """Decodes and clears the `LOBBY_MSG` notifications sent to the client (not the replies)."""
  msgs = [gsm.Message(None, in_buf=buf) for buf in clt.conn.sent]
  msgs = [m.dl.lst for m in msgs if m.header.type == gsm.MESSAGE_TYPE.LOBBY_MSG and m.dl.lst[0] not in ('38', '39')]
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

class MatchStartTests(unittest.TestCase):
  """The host starts the game."""
  def setUp(self):
    lobby.g_clients.clear()
    lobby.g_rooms.clear()

  def test_start(self):
    alice = login('alice')
    bob = login('bob')
    res = request(alice, gsm.MESSAGE_TYPE.LOBBY_MSG, ['12', ['1', "alice's Game", 'HEROES_657d2c2ebadc6a1d', '7', '2', '0', ROOM_INFO, '', '', 'HEROES_c83663d1c69a2024', b'']])
    room_id = res.dl.lst[1][1][0]
    for clt in [alice, bob]:
      request(clt, gsm.MESSAGE_TYPE.LOBBY_MSG, ['24', [room_id, '', '448', '0', 'HEROES_c83663d1c69a2024']])
    for clt in [alice, bob]:
      clt.conn.sent.clear()
    res = request(alice, gsm.MESSAGE_TYPE.LOBBY_MSG, ['15', [room_id]])
    self.assertEqual(res.dl.lst, ['38', ['15', [room_id]]])
    res = request(alice, gsm.MESSAGE_TYPE.LOBBY_MSG, ['33', [room_id, b'', '0', '']])
    self.assertEqual(res.dl.lst, ['38', ['33', [room_id]]])
    # everyone in the room, the host included: group id, data, port, host ip, host local ip
    for clt in [alice, bob]:
      self.assertEqual(notifs(clt), [['56', [room_id, b'', '0', '127.0.0.1', '192.168.0.41']]])

  def test_notifications_no_reply(self):
    alice = login('alice')
    for sub in ['34', '35', '44', '70']:
      self.assertIs(request(alice, gsm.MESSAGE_TYPE.LOBBY_MSG, [sub, ['1000']]), lobby.NO_REPLY)

class ProtocolTests(unittest.TestCase):
  def setUp(self):
    lobby.g_clients.clear()
    lobby.g_rooms.clear()

  def test_lobby_server_login_id(self):
    clt = make_client('alice')
    # the second field is a flag, not the server id
    res = request(clt, gsm.MESSAGE_TYPE.LOBBYSERVERLOGIN, ['alice', '0', '192.168.0.41', '255.255.255.0', '0'])
    self.assertEqual(res.dl.lst, ['210', ['1']])

  def test_group_info_get_no_extra_reply(self):
    clt = login('alice')
    clt.conn.sent.clear()
    self.assertIs(request(clt, gsm.MESSAGE_TYPE.LOBBY_MSG, ['9', ['1', '384']]), lobby.NO_REPLY)
    self.assertEqual([m[0] for m in notifs(clt)], ['53'])

  def test_ds_flags_any(self):
    alice = login('alice')
    res = request(alice, gsm.MESSAGE_TYPE.LOBBY_MSG, ['12', ['1', "alice's Game", 'HEROES_657d2c2ebadc6a1d', '7', '2', '0', ROOM_INFO, '', '', 'HEROES_c83663d1c69a2024', b'']])
    room_id = res.dl.lst[1][1][0]
    # OPEN only (0x2) carries the dsFlags value before max players
    request(alice, gsm.MESSAGE_TYPE.LOBBY_MSG, ['31', [room_id, str(0x2 | 0x8), '16', '4']])
    self.assertEqual(lobby.find_room(int(room_id)).gs_room.max_players, 4)
class RatedMatchTests(unittest.TestCase):
  """START_MATCH, SUBMIT_MATCH and MATCH_FINISH."""
  def setUp(self):
    lobby.g_clients.clear()
    lobby.g_rooms.clear()
    lobby.g_matches.clear()

  def test_match(self):
    alice = login('alice')
    bob = login('bob')
    res = request(alice, gsm.MESSAGE_TYPE.LOBBY_MSG, ['12', ['1', "alice's Game", 'HEROES_657d2c2ebadc6a1d', '7', '2', '0', ROOM_INFO, '', '', 'HEROES_c83663d1c69a2024', b'']])
    room_id = res.dl.lst[1][1][0]
    for clt in [alice, bob]:
      request(clt, gsm.MESSAGE_TYPE.LOBBY_MSG, ['24', [room_id, '', '448', '0', 'HEROES_c83663d1c69a2024']])
    for clt in [alice, bob]:
      clt.conn.sent.clear()
    res = request(alice, gsm.MESSAGE_TYPE.LOBBY_MSG, ['17', [room_id, '1', '0']])
    self.assertEqual(res.dl.lst, ['38', ['17', [room_id]]])
    started = notifs(alice)
    self.assertEqual(started, notifs(bob))
    self.assertEqual(started[0][0], '62')
    group, match_id = started[0][1]
    self.assertEqual(group, room_id)
    for name, values in [('alice', ['1', '0']), ('bob', ['0', '1'])]:
      res = request(alice if name == 'alice' else bob, gsm.MESSAGE_TYPE.LOBBY_MSG, ['30', [match_id, '0', [name, '1', '2', '3', values]]])
      self.assertEqual(res.dl.lst, ['38', ['30', [match_id]]])
    res = request(alice, gsm.MESSAGE_TYPE.LOBBY_MSG, ['45', [room_id]])
    self.assertEqual(res.dl.lst, ['38', ['45', [room_id]]])
    final = [['71', [match_id, '38', [['alice', ['1', '0']], ['bob', ['0', '1']]]]]]
    self.assertEqual(notifs(alice), final)
    self.assertEqual(notifs(bob), final)

  def test_finish_without_results(self):
    alice = login('alice')
    res = request(alice, gsm.MESSAGE_TYPE.LOBBY_MSG, ['12', ['1', "alice's Game", 'HEROES_657d2c2ebadc6a1d', '7', '2', '0', ROOM_INFO, '', '', 'HEROES_c83663d1c69a2024', b'']])
    room_id = res.dl.lst[1][1][0]
    request(alice, gsm.MESSAGE_TYPE.LOBBY_MSG, ['24', [room_id, '', '448', '0', 'HEROES_c83663d1c69a2024']])
    request(alice, gsm.MESSAGE_TYPE.LOBBY_MSG, ['17', [room_id, '1', '0']])
    match_id = notifs(alice)[-1][1][1]
    request(alice, gsm.MESSAGE_TYPE.LOBBY_MSG, ['45', [room_id]])
    self.assertEqual(notifs(alice), [['71', [match_id, '39', ['0']]]])

def values(result: str, race: str = '3'):
  """22 values as the game submits them: result, race, ..., average level (16.16), seconds played."""
  v = ['0'] * 22
  v[0], v[1], v[16], v[17] = result, race, '65536', '87'
  return v

class RatedLadderTests(unittest.TestCase):
  """Rated results update the ladder (#61)."""
  def setUp(self):
    lobby.g_clients.clear()
    lobby.g_rooms.clear()
    lobby.g_matches.clear()
    import accounts
    accounts.configure(accounts.MEMORY)
    self.accounts = accounts
    self.alice = accounts.login('alice', 'pw')
    self.bob = accounts.login('bob', 'pw')

  def tearDown(self):
    self.accounts.configure(None)

  def play(self, alice_says: str, bob_says: str):
    alice, bob = login('alice'), login('bob')
    res = request(alice, gsm.MESSAGE_TYPE.LOBBY_MSG, ['12', ['1', "alice's Game", 'HEROES_657d2c2ebadc6a1d', '7', '2', '0', ROOM_INFO, '', '', 'HEROES_c83663d1c69a2024', b'']])
    room_id = res.dl.lst[1][1][0]
    for clt in [alice, bob]:
      request(clt, gsm.MESSAGE_TYPE.LOBBY_MSG, ['24', [room_id, '', '448', '0', 'HEROES_c83663d1c69a2024']])
    request(alice, gsm.MESSAGE_TYPE.LOBBY_MSG, ['17', [room_id, '1', '0']])
    match_id = notifs(alice)[-1][1][1]
    # every client submits every player's values
    for clt, says in [(alice, alice_says), (bob, bob_says)]:
      request(clt, gsm.MESSAGE_TYPE.LOBBY_MSG, ['30', [match_id, '0', ['alice', '21', '22', '0', values(says)],
        ['bob', '21', '22', '0', values('0' if says == '1' else '1', '5')]]])
    request(alice, gsm.MESSAGE_TYPE.LOBBY_MSG, ['45', [room_id]])
    self.assertEqual(notifs(bob)[-1][0], '71')

  def stats(self, account):
    return self.accounts.ladder_stats(account, 'HEROES_657d2c2ebadc6a1d/1')

  def test_winner_and_loser(self):
    self.play('1', '1')
    a, b = self.stats(self.alice), self.stats(self.bob)
    self.assertEqual((a['RATING'], b['RATING']), (1516, 1484))
    self.assertEqual((a['WINS'], a['LOSSES'], b['WINS'], b['LOSSES']), (1, 0, 0, 1))
    self.assertEqual((a['W_DUNGEON'], a['G_DUNGEON'], b['L_INFERNO']), (1, 1, 1))
    self.assertEqual((a['TOT_TIME_PLAYED'], a['AVERAGE_HERO_LEVEL'], a['CUR_WINS_STREAK']), (87, 65536, 1))
    # a second win: the rating moves less, the streak grows
    self.play('1', '1')
    a = self.stats(self.alice)
    self.assertEqual((a['RATING'], a['GAMES_PLAYED'], a['MAX_WINS_STREAK']), (1531, 2, 2))

  def test_disputed_not_rated(self):
    # each claims the win
    self.play('1', '0')
    self.assertEqual(self.stats(self.alice), {})
    self.assertEqual(self.stats(self.bob), {})

class RatingsTests(unittest.TestCase):
  def test_apply(self):
    import ratings
    won = [int(x) for x in values('1')]
    lost = [int(x) for x in values('2', '9')]
    lost[17] = -5
    out = ratings.apply([(1, won), (2, lost)], {1: {'RATING': 1600, 'GAMES_PLAYED': 1, 'AVERAGE_HERO_LEVEL': 3 << 16}})
    # the favourite wins: a small gain; a disconnection is a loss; an unknown race and a negative time count nothing
    self.assertEqual((out[1]['RATING'], out[2]['RATING']), (1612, 1488))
    self.assertEqual(out[1]['AVERAGE_HERO_LEVEL'], 2 << 16)
    self.assertEqual((out[2]['LOSSES'], out[2]['DISCONNECTIONS'], out[2]['TOT_TIME_PLAYED'], out[2]['G_HEAVEN']), (1, 1, 0, 0))
    self.assertEqual(len(out[1]), 42)

  def test_parse(self):
    import ratings
    self.assertEqual(len(ratings.parse(values('1'))), 22)
    self.assertIsNone(ratings.parse(['1', '0']))
    self.assertIsNone(ratings.parse(['x'] * 22))

class JoinOrderTests(unittest.TestCase):
  def setUp(self):
    lobby.g_clients.clear()
    lobby.g_rooms.clear()

  def test_room_info_after_join_reply(self):
    alice = login('alice')
    res = request(alice, gsm.MESSAGE_TYPE.LOBBY_MSG, ['12', ['1', "alice's Game", 'HEROES_657d2c2ebadc6a1d', '7', '2', '0', ROOM_INFO, '', '', 'HEROES_c83663d1c69a2024', b'']])
    room_id = res.dl.lst[1][1][0]
    alice.conn.sent.clear()
    request(alice, gsm.MESSAGE_TYPE.LOBBY_MSG, ['24', [room_id, '', '448', '0', 'HEROES_c83663d1c69a2024']])
    sent = [gsm.Message(None, in_buf=buf).dl.lst for buf in alice.conn.sent]
    # GROUP_INFO with the room, the join reply, then the members (alice herself) as MEMBER_JOIN
    self.assertEqual([m[0] for m in sent[:3]], ['53', '38', '50'])
    self.assertEqual(sent[1], ['38', ['24', [room_id]]])
    self.assertEqual(sent[2][1][:3], ['alice', '0', room_id])

if __name__ == '__main__':
  unittest.main()
