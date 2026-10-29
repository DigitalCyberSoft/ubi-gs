import sys, os, unittest
# relative module import stuff
root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.append(root_dir)
import h5_data, utils

class H5_SerializerTests(unittest.TestCase):
  """Tests for `H5_RoomInfo` data buffer serialization."""
  def test_serialize(self):
    ref_buf = b"\x04\x08\x04\x00\x00\x00\x01%\x03\x00\x00\x02\x08\xff\xff\xff\xff\x03\x08\xff\xff\xff\xff\x04$\x02 \x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x06\x02\x00\x070m\x00i\x00m\x00a\x00k\x00'\x00s\x00 \x00G\x00a\x00m\x00e\x00\x08,t\x00h\x00e\x00p\x00a\x00s\x00s\x00w\x00o\x00r\x00d\x00\t\x02\x01\n\x02\x00\x0b\x02\x00\x0c\x02\x00\r\x08\xff\xff\xff\xff\x0e\x08\x01\x00\x00\x00\x0ff\x02b/Maps/Multiplayer/L4/L4.xdb#xpointer(/AdvMapDesc)\x13@\x01\x08\x06\x00\x00\x00\x020\x01\x00\x00\x00\x01\x00\x00\x00\x01\x00\x00\x00\x01\x00\x00\x00\x01\x00\x00\x00\x01\x00\x00\x00\x14\x08\x06\x00\x00\x00\x15\x08\x03\x00\x00\x00\x16\x18\x03\x08\x00\x00\x00\x00\x04\x08\r\x00\x00\x00\x17\x08<\x00\x01\x00\x18\x02\x00\x19 \x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x1a\x02\x00\x1bT\x03\x08\x01\x00\x00\x00\x04\x08\r\x00\x00\x00\x01 autosave_enabled\x02\x14\x02\x08\x00\x00\x00\x00\x03\x040\x00\x1c\x02\x00\x1d\x08\xb1\xaf\xdd\xc8\x1e\x08\x01\x00\x00\x00\x1fv\x02r/Maps/Multiplayer/L4/map-tag.xdb#xpointer(/AdvMapDescTag) \ngoal3!\x00%\x08\x00\x00\x00\x00&\x08\x00\x00\x00\x00'\x08\x00\x00\x00\x00(\x02\x00\x00\x00\x02\x00\x05\x00"
    info = h5_data.H5_RoomInfo()
    info.group_id = -1
    info.lobby_sv_id = -1
    info.host_ip = h5_data.H5_IP()
    info.host_logic_init = False
    info.group_name = "mimak's Game"
    info.password = "thepassword"
    info.is_pwd_protected = True
    info.ghost_mode = False
    info.quick_combat = False
    info.fast_combat_turns = False
    info.time_limit = -1
    info.difficulty_id = 1
    info.map_desc = "/Maps/Multiplayer/L4/L4.xdb#xpointer(/AdvMapDesc)"
    info.teams = [1, 1, 1, 1, 1, 1]
    info.max_players = 6
    info.map_size = 3
    info.player_infos = []
    info.game_version = 0x1003C # 1.60
    info.is_saved = False
    info.guid = "00000000-0000-0000-0000-000000000000"
    info.ubi_send_results_wait = False
    info.options = {"autosave_enabled": (0, "0")}
    info.is_arena = False
    info.host_checksum = 0xC8DDAFB1
    info.adventure_type = 1
    info.map_desc_tag = "/Maps/Multiplayer/L4/map-tag.xdb#xpointer(/AdvMapDescTag)"
    info.map_goal = "goal3"
    info.arena_map_name = ""
    info.int104 = 0
    info.int108 = 0
    info.combat_turn_speed = 0
    info.flag110 = False

    buf = h5_data.H5_Serializer().serialize_roominfo(info)
    self.assertEqual(buf, ref_buf)

  def test_deserialize(self):
    data = b"\x04\x08\x04\x00\x00\x00\x01\xf9\x02\x00\x00\x02\x08\xe8\x03\x00\x00\x03\x08\x01\x00\x00\x00\x04$\x02 \x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x06\x02\x01\x070m\x00i\x00m\x00a\x00k\x00'\x00s\x00 \x00G\x00a\x00m\x00e\x00\x08\x00\t\x02\x00\n\x02\x00\x0b\x02\x00\x0c\x02\x00\r\x08\xff\xff\xff\xff\x0e\x08\x01\x00\x00\x00\x0ff\x02b/Maps/Multiplayer/L4/L4.xdb#xpointer(/AdvMapDesc)\x13@\x01\x08\x06\x00\x00\x00\x020\x01\x00\x00\x00\x01\x00\x00\x00\x01\x00\x00\x00\x01\x00\x00\x00\x01\x00\x00\x00\x01\x00\x00\x00\x14\x08\x06\x00\x00\x00\x15\x08\x03\x00\x00\x00\x16\x18\x03\x08\x00\x00\x00\x00\x04\x08\r\x00\x00\x00\x17\x08<\x00\x01\x00\x18\x02\x00\x19 \x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x1a\x02\x00\x1bT\x03\x08\x01\x00\x00\x00\x04\x08\r\x00\x00\x00\x01 autosave_enabled\x02\x14\x02\x08\x00\x00\x00\x00\x03\x040\x00\x1c\x02\x00\x1d\x08\xb1\xaf\xdd\xc8\x1e\x08\x01\x00\x00\x00\x1fv\x02r/Maps/Multiplayer/L4/map-tag.xdb#xpointer(/AdvMapDescTag) \ngoal3!\x00%\x08\x00\x00\x00\x00&\x08\x00\x00\x00\x00'\x08\x00\x00\x00\x00(\x02\x00\x00\x00\x02\x00\x05\x00"
    serializer = h5_data.H5_Serializer()
    room_info = serializer.deserialize_roominfo(data)

    # top-level streams, as-written order
    stream_serial_mode = serializer.streams[h5_data.H5_STREAM_TYPE.SERIALIZATION_MODE.name]
    self.assertEqual(stream_serial_mode.size_4b, False)
    self.assertEqual(stream_serial_mode.size, 4)
    self.assertEqual(utils.read_u32(stream_serial_mode.data), 4)

    stream = serializer.streams[h5_data.H5_STREAM_TYPE.COMPRESSED_DATA.name]
    self.assertEqual(stream.size_4b, True)
    self.assertEqual(stream.size, 380)

    stream = serializer.streams[h5_data.H5_STREAM_TYPE.STRUCTURE.name]
    self.assertEqual(stream.size_4b, False)
    self.assertEqual(stream.size, 0)

    stream = serializer.streams[h5_data.H5_STREAM_TYPE.RAW_DATA.name]
    self.assertEqual(stream.size_4b, False)
    self.assertEqual(stream.size, 0)

    stream = serializer.streams[h5_data.H5_STREAM_TYPE.LOOKUP_TABLE.name]
    self.assertEqual(stream.size_4b, False)
    self.assertEqual(stream.size, 0)

    self.assertRaises(KeyError, lambda: serializer.streams[h5_data.H5_STREAM_TYPE.METADATA.name])

    # data
    self.assertEqual(room_info.group_id, 1000)
    self.assertEqual(room_info.lobby_sv_id, 1)
    self.assertEqual(room_info.host_ip.family, 0)
    self.assertEqual(room_info.host_ip.port, 0)
    self.assertEqual(room_info.host_ip.ip, "0.0.0.0")
    self.assertEqual(room_info.host_ip.zero, bytes(8))
    self.assertEqual(room_info.host_logic_init, True)
    self.assertEqual(room_info.group_name, "mimak's Game")
    self.assertEqual(room_info.password, "")
    self.assertEqual(room_info.is_pwd_protected, False)
    self.assertEqual(room_info.ghost_mode, False)
    self.assertEqual(room_info.quick_combat, False)
    self.assertEqual(room_info.fast_combat_turns, False)
    self.assertEqual(room_info.time_limit, -1)
    self.assertEqual(room_info.difficulty_id, 1)
    self.assertEqual(room_info.map_desc, "/Maps/Multiplayer/L4/L4.xdb#xpointer(/AdvMapDesc)")
    self.assertEqual(room_info.teams, [1, 1, 1, 1, 1, 1])
    self.assertEqual(room_info.max_players, 6)
    self.assertEqual(room_info.map_size, 3)
    self.assertEqual(room_info.player_infos, [])
    self.assertEqual(room_info.game_version, 0x1003C) # 1.60
    self.assertEqual(room_info.is_saved, False)
    self.assertEqual(room_info.guid, "00000000-0000-0000-0000-000000000000")
    self.assertEqual(room_info.ubi_send_results_wait, False)
    self.assertEqual(room_info.options, {"autosave_enabled": (0, "0")})
    self.assertEqual(room_info.is_arena, False)
    self.assertEqual(room_info.host_checksum, 0xC8DDAFB1)
    self.assertEqual(room_info.adventure_type, 1)
    self.assertEqual(room_info.map_desc_tag, "/Maps/Multiplayer/L4/map-tag.xdb#xpointer(/AdvMapDescTag)")
    self.assertEqual(room_info.map_goal, "goal3")
    self.assertEqual(room_info.arena_map_name, "")
    self.assertEqual(room_info.int104, 0)
    self.assertEqual(room_info.int108, 0)
    self.assertEqual(room_info.combat_turn_speed, 0)
    self.assertEqual(room_info.flag110, False)

  def test_e2e(self):
    input = b'\x04\x08\x04\x00\x00\x00\x01{\x03\x00\x00\x02\x08\xe8\x03\x00\x00\x03\x08\x01\x00\x00\x00\x04$\x02 \x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x06\x02\x01\x070m\x00i\x00m\x00a\x00k\x00\'\x00s\x00 \x00G\x00a\x00m\x00e\x00\x08\x00\t\x02\x00\n\x02\x00\x0b\x02\x00\x0c\x02\x00\r\x08\xff\xff\xff\xff\x0e\x08\x01\x00\x00\x00\x0ff\x02b/Maps/Multiplayer/L4/L4.xdb#xpointer(/AdvMapDesc)\x13@\x01\x08\x06\x00\x00\x00\x020\x01\x00\x00\x00\x01\x00\x00\x00\x01\x00\x00\x00\x01\x00\x00\x00\x01\x00\x00\x00\x01\x00\x00\x00\x14\x08\x06\x00\x00\x00\x15\x08\x03\x00\x00\x00\x16\x9a\x03\x08\x01\x00\x00\x00\x04\x08\r\x00\x00\x00\x01\x08\x00\x00\x00\x00\x02r\x02\nmimak\x03$\x02 \x02\x00\x1ee\x01\x00\x00\x7f\x00\x00\x00\x00\x00\x00\x00\x00\x04,\x02\x04\xb8"\x03 \xc0\xa8\x05i\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x05\x08\xff\xff\xff\xff\x17\x08<\x00\x01\x00\x18\x02\x00\x19 \x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x1a\x02\x00\x1bT\x03\x08\x01\x00\x00\x00\x04\x08\r\x00\x00\x00\x01 autosave_enabled\x02\x14\x02\x08\x00\x00\x00\x00\x03\x040\x00\x1c\x02\x00\x1d\x08\xb1\xaf\xdd\xc8\x1e\x08\x01\x00\x00\x00\x1fv\x02r/Maps/Multiplayer/L4/map-tag.xdb#xpointer(/AdvMapDescTag) \ngoal3!\x00%\x08\x00\x00\x00\x00&\x08\x00\x00\x00\x00\'\x08\x00\x00\x00\x00(\x02\x00\x00\x00\x02\x00\x05\x00'
    info = h5_data.H5_Serializer().deserialize_roominfo(input)
    output = h5_data.H5_Serializer().serialize_roominfo(info)
    self.assertEqual(input, output)

class H5_PlayerInfoTests(unittest.TestCase):
  """Tests for `PlayerInfo` structures of `RoomInfo`."""
  def test_empty_slot(self):
    # a player slot without a player, sent by the host of a 2-player map
    buf = bytes.fromhex("02 00 03 24 02 20" + " 00" * 16 + " 04 2c 02 04 00 00 03 20" + " 00" * 16 + " 05 08 00 00 00 00")
    info = h5_data.H5_PlayerInfo(buf)
    self.assertEqual(info.username, "")
    self.assertEqual(info.local_port, 0)
    self.assertEqual(info.serialize(), buf)

  def test_player(self):
    buf = bytes.fromhex("02 0a 61 6c 69 63 65 03 24 02 20 02 00 22 b8 7f 00 00 01" + " 00" * 8 + " 04 2c 02 04 b8 22 03 20 c0 a8 00 29" + " 00" * 12 + " 05 08 ff ff ff ff")
    info = h5_data.H5_PlayerInfo(buf)
    self.assertEqual(info.username, "alice")
    self.assertEqual(info.ext_ip.ip, "127.0.0.1")
    self.assertEqual(info.ext_ip.port, 8888)
    self.assertEqual(info.local_port, 8888)
    self.assertEqual(info.local_ip.ip, "192.168.0.41")
    self.assertEqual(info.serialize(), buf)

  def test_code_page_name(self):
    # a name in the game's code page (0xE9 is 'é' in Windows-1252) parses and comes back unchanged (#55)
    info = h5_data.H5_PlayerInfo()
    info.username = b'caf\xe9'.decode('latin-1')
    buf = info.serialize()
    self.assertIn(b'caf\xe9', buf)
    self.assertEqual(h5_data.H5_PlayerInfo(buf).serialize(), buf)

  def test_later_fields(self):
    # fields after the known ones go back as they came
    buf = bytes.fromhex("02 0a 61 6c 69 63 65 03 24 02 20 02 00 22 b8 7f 00 00 01" + " 00" * 8 + " 04 2c 02 04 b8 22 03 20 c0 a8 00 29" + " 00" * 12 + " 05 08 ff ff ff ff 06 02 07")
    info = h5_data.H5_PlayerInfo(buf)
    self.assertEqual(info.rest, bytes([6, 2, 7]))
    self.assertEqual(info.serialize(), buf)

class H5_RoomInfoRoundTripTests(unittest.TestCase):
  """Room info sent back to the players must match what the host sent."""
  def test_two_players(self):
    # host 'alice' (id 0) and 'bob' (id 1) on a 2-player map; ids are written in the host's order
    buf = bytes.fromhex(
      "04080400000001df0300000208e803000003080100000004240220000000000000000000000000000000000602010730"
      "61006c00690063006500270073002000470061006d00650008000902000a02000b02000c02000d08ffffffff0e080100"
      "00000f6602622f4d6170732f4d756c7469706c617965722f53342f53342e7864622378706f696e746572282f4164764d"
      "617044657363291320010802000000021001000000010000001408020000001508010000001619010000030802000000"
      "04080d000000010801000000010800000000026e0206626f620324022000000000000000000000000000000000042c02"
      "0400000320000000000000000000000000000000000508ffffffff0272020a616c696365032402200000000000000000"
      "0000000000000000042c020400000320000000000000000000000000000000000508ffffffff17083c00010018020019"
      "20000000000000000000000000000000001a02001b5403080100000004080d00000001206175746f736176655f656e61"
      "626c65640214020800000000030430001c02001d087737aa291e08010000001f7602722f4d6170732f4d756c7469706c"
      "617965722f53342f6d61702d7461672e7864622378706f696e746572282f4164764d61704465736354616729200a676f"
      "616c352100250800000000260800000000270800000000280200000002000500"
    )
    info = h5_data.H5_Serializer().deserialize_roominfo(buf)
    self.assertEqual([(p.key, p.username) for p in info.player_infos], [(1, 'bob'), (0, 'alice')])
    self.assertEqual(h5_data.H5_Serializer().serialize_roominfo(info), buf)

class H5_ExpansionRoomInfoTests(unittest.TestCase):
  """The room info of Hammers of Fate and Tribes of the East, as each sends it in CREATE_ROOM: their fields
  differ from Heroes V's, and each must come back as it came but for the fields the server sets."""
  HOF = bytes.fromhex(
      "040804000000018d0300000208ffffffff0308ffffffff04240220000000000000000000000000000000000602000730"
      "61006c00690063006500270073002000470061006d00650008000902000a02000b02000c02000d08ffffffff0e080100"
      "00000f7602722f4d6170732f4d756c7469706c617965722f41314d332f41314d332f6d61702e7864622378706f696e74"
      "6572282f4164764d61704465736329132001080200000002100100000001000000140802000000150802000000161803"
      "080000000004080d0000001708010002001802001920000000000000000000000000000000001a02001b540308010000"
      "0004080d00000001206175746f736176655f656e61626c65640214020800000000030430001c02001d0868b086741e08"
      "010000001f8402802f4d6170732f4d756c7469706c617965722f41314d332f41314d332f6d61702d7461672e78646223"
      "78706f696e746572282f4164764d61704465736354616729200a676f616c33210022020023b002020003080100000004"
      "0800000000058e02080000000003080000000004340330656d707479206f626a65637420706c616365686f6c64657205"
      "0800000000060800000000070200080c02080000000009000a02000b0200240200000002000500"
  )
  TOE = bytes.fromhex(
      "04080400000001030400000208ffffffff0308ffffffff04240220000000000000000000000000000000000602000730"
      "6300610072006f006c00270073002000470061006d00650008000902000a02000b02000d08ffffffff0e08010000000f"
      "6e026a2f4d6170732f4d756c7469706c617965722f41324d31332f6d61702e7864622378706f696e746572282f416476"
      "4d6170446573632913200108020000000210010000000100000014080200000015080100000016180308000000000408"
      "0d0000001708010003001802001920000000000000000000000000000000001a02001b5403080100000004080d000000"
      "01206175746f736176655f656e61626c65640214020800000000030430001c02001d08a0fffb361e08010000001f7c02"
      "782f4d6170732f4d756c7469706c617965722f41324d31332f6d61702d7461672e7864622378706f696e746572282f41"
      "64764d61704465736354616729200a676f616c33210022020023d2020200030801000000040800000000050006ac0208"
      "0000000003080000000004340330656d707479206f626a65637420706c616365686f6c64657205080000000006080000"
      "0000070200080c02080000000009000a02000b02000c08000000000d08000000000e0200240200250800000000260800"
      "000000270800000000280200293c02080000000003080000000004080000000005080000000006080000000000000200"
      "0500"
  )
  BASE = [2, 3, 4, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32, 33]

  def check(self, buf, ids, version):
    info = h5_data.H5_Serializer().deserialize_roominfo(buf)
    self.assertEqual([idx for idx, _ in info.fields], ids)
    self.assertEqual(info.game_version, version)
    self.assertEqual(info.group_id, -1)
    self.assertEqual(h5_data.H5_Serializer().serialize_roominfo(info), buf)
    # as the lobby does: the ids, the host and a player
    info.group_id, info.lobby_sv_id = 1000, 1
    info.host_ip.ip, info.host_ip.port = "10.0.0.2", 9000
    player = h5_data.H5_PlayerInfo()
    player.username = "alice"
    info.player_infos.append(player)
    again = h5_data.H5_Serializer().deserialize_roominfo(h5_data.H5_Serializer().serialize_roominfo(info))
    self.assertEqual((again.group_id, again.lobby_sv_id, again.host_ip.ip, again.host_ip.port), (1000, 1, "10.0.0.2", 9000))
    self.assertEqual([p.username for p in again.player_infos], ["alice"])
    for (idx, data), (_, orig) in zip(again.fields, info.fields):
      if idx not in h5_data.H5_RoomInfo.REWRITTEN:
        self.assertEqual(data, orig, idx)

  def test_hammers_of_fate(self):
    # 34-36 after 33, no 37-40
    self.check(self.HOF, self.BASE + [34, 35, 36], 0x20001)

  def test_tribes_of_the_east(self):
    # no 12; 34-41 after 33
    self.check(self.TOE, [i for i in self.BASE if i != 12] + [34, 35, 36, 37, 38, 39, 40, 41], 0x30001)

  def test_unknown_field(self):
    data = h5_data.H5_Serializer().data_stream(self.HOF).data + bytes([99, 10]) + b"later"
    info = h5_data.H5_RoomInfo(data)
    self.assertEqual(info.fields[-1], (99, b"later"))
    self.assertTrue(info.serialize().endswith(bytes([99, 10]) + b"later"))

  def test_repeated_host(self):
    data = h5_data.H5_Serializer().data_stream(self.HOF).data
    host = bytes([4, 0x24, 2, 0x20]) + bytes(16)
    self.assertRaises(ValueError, h5_data.H5_RoomInfo, data + host)

class H5_IPTests(unittest.TestCase):
  """Tests for address structures of `RoomInfo` and `PlayerInfo`."""
  def test_sockaddr_in(self):
    # host's public address advertised in room info, 127.0.0.1:8888
    buf = bytes([0x02, 0x00, 0x22, 0xB8, 0x7F, 0x00, 0x00, 0x01]) + bytes(8)
    ip = h5_data.H5_IP(buf)
    self.assertEqual(ip.family, 2)
    self.assertEqual(ip.port, 8888)
    self.assertEqual(ip.ip, "127.0.0.1")
    self.assertEqual(ip.serialize(), buf)

  def test_local_ip(self):
    buf = bytes([0xC0, 0xA8, 0x00, 0x29]) + bytes(12)
    ip = h5_data.H5_LocalIP(buf)
    self.assertEqual(ip.ip, "192.168.0.41")
    self.assertEqual(ip.serialize(), buf)

if __name__ == '__main__':
  unittest.main()
