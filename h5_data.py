from enum import Enum
from ctypes import c_int32, c_uint32
from group import Room
import socket
from utils import read_u32, read_as_u32_list, read_u16, read_u16_be, write_u32, write_u32_list, write_u16, write_u16_be
from uuid import UUID
from utils import GAME_ENCODING

class H5_STREAM_TYPE(Enum):
  """Main file stream type for serialized buffers; names assumed."""
  STRUCTURE = 0
  """Empty."""
  COMPRESSED_DATA = 1
  """Contains serialized data, optionally compressed with zlib."""
  RAW_DATA = 2
  """Empty."""
  METADATA = 3
  """Unused."""
  SERIALIZATION_MODE = 4
  """Contains pointer search serialization mode."""
  LOOKUP_TABLE = 5
  """Empty."""

class H5_Stream:
  """File stream/substream (structure field) for serialized buffers."""
  def __init__(self, buf: bytes = None):
    if buf is None:
      self.id = -1
      self.size = -1
      self.size_4b = False
      self.data = bytes()
    else:
      self.read(buf)

  def read(self, buf: bytes):
    """Returns nb of bytes read."""
    self.id = int(buf[0])
    # 4-byte size
    if int(buf[1]) & 1 == 1:
      self.size_4b = True
      self.size = read_u32(buf[1:]) >> 1
      if len(buf) < self.size + 5:
        raise ValueError("Stream's declared size exceeds the buffer size.")
      self.data = buf[5:self.size + 5]
    # 1-byte size
    else:
      self.size_4b = False
      self.size = int(buf[1]) >> 1
      if len(buf) < self.size + 2:
        raise ValueError("Stream's declared size exceeds the buffer size.")
      self.data = buf[2:self.size + 2]

  def write(self):
    """Returns a serialized buffer."""
    buf = bytearray([self.id])
    if self.size_4b or self.size > 127:
      buf.extend(write_u32((self.size << 1) | 1))
    else:
      buf.append(self.size << 1)
    buf.extend(self.data)
    return bytes(buf)

  def __len__(self):
    return (5 if self.size_4b else 2) + self.size

class H5_IP:
  """`sockaddr_in` structure (16B): address family, port and IPv4 address, both in network byte order."""
  def __init__(self, buf: bytes = None):
    if buf is None:
      self.family = 0
      self.port = 0
      self.ip = "0.0.0.0"
      self.zero = bytes(8)
    else:
      if len(buf) != 16:
        raise ValueError("H5_IP: invalid input, deserialization failed")
      # AF_INET (2) when set
      self.family = read_u16(buf[0:2])
      self.port = read_u16_be(buf[2:4])
      self.ip = socket.inet_ntoa(buf[4:8])
      # sin_zero
      self.zero = buf[8:16]

  def serialize(self):
    """Returns a serialized buffer."""
    buf = bytearray()
    buf.extend(write_u16(self.family))
    buf.extend(write_u16_be(self.port))
    try:
      buf.extend(socket.inet_aton(self.ip))
    except OSError:
      raise ValueError("H5_IP: invalid IPv4 address, serialization failed")
    buf.extend(self.zero)
    return bytes(buf)

  def __repr__(self):
    return f"'{self.ip}:{self.port}'"

class H5_LocalIP:
  """Local (private) IPv4 address of `PlayerInfo` in network byte order, padded to 16B."""
  def __init__(self, buf: bytes = None):
    if buf is None:
      self.ip = "0.0.0.0"
      self.padding = bytes(12)
    else:
      if len(buf) != 16:
        raise ValueError("H5_LocalIP: invalid input, deserialization failed")
      self.ip = socket.inet_ntoa(buf[0:4])
      self.padding = buf[4:16]

  def serialize(self):
    """Returns a serialized buffer."""
    try:
      ip = socket.inet_aton(self.ip)
    except OSError:
      raise ValueError("H5_LocalIP: invalid IPv4 address, serialization failed")
    return ip + self.padding

  def __repr__(self):
    return f"'{self.ip}'"

class H5_Field:
  """A structure field the server reads and writes: its index, the attribute holding it, its name, its size
  (`None` for any) and how to read and write it. The lobby changes the `required` fields, so they must be
  there, in the form it reads."""
  def __init__(self, idx: int, attr: str, name: str, size: int | None, read, write, required: bool = False):
    self.idx = idx
    self.attr = attr
    self.name = name
    self.size = size
    self.read = read
    self.write = write
    self.required = required

def read_fields(obj, buf: bytes, fields: list[H5_Field], cl_name: str) -> list[tuple[int, bytes | None]]:
  """Reads a structure's fields into `obj` by index and returns their layout, which `write_fields` keeps:
  `(idx, None)` for a field read, `(idx, data)` for a field kept as it came.

  The expansions don't lay out the room info like 1.60 (Hammers of Fate's differs after field 33, Tribes of
  the East's after field 11), so a field the server doesn't know, or can't read, is kept as it came and
  written back unchanged. Only the required fields must be there and read."""
  by_idx = {field.idx: field for field in fields}
  layout: list[tuple[int, bytes | None]] = []
  seen: set[int] = set()
  read: set[int] = set()
  pos = 0
  while pos < len(buf):
    stream = H5_Stream(buf[pos:])
    pos += len(stream)
    field = by_idx.get(stream.id)
    if field is None:
      layout.append((stream.id, bytes(stream.data)))
      continue
    if stream.id in seen:
      # the lobby must change every copy the game could read
      raise ValueError(f"{cl_name}: field {field.name} ({field.idx}) repeated")
    seen.add(stream.id)
    try:
      if field.size is not None and stream.size != field.size:
        raise ValueError(f"{cl_name}: missing/invalid field - {field.name} ({field.idx})")
      setattr(obj, field.attr, field.read(stream.data))
    except Exception:
      if field.required:
        raise
      layout.append((stream.id, bytes(stream.data)))
      continue
    read.add(stream.id)
    layout.append((stream.id, None))
  for field in fields:
    if field.required and field.idx not in read:
      raise ValueError(f"{cl_name}: missing/invalid field - {field.name} ({field.idx})")
  return layout

def write_fields(obj, layout: list[tuple[int, bytes | None]] | None, fields: list[H5_Field]) -> bytes:
  """Writes a structure's fields in its layout, or, for a structure made here, every field in their order (1.60's)."""
  by_idx = {field.idx: field for field in fields}
  if layout is None:
    layout = [(field.idx, None) for field in fields]
  buf = bytearray()
  stream = H5_Stream()
  for idx, data in layout:
    if data is None:
      field = by_idx[idx]
      data = field.write(getattr(obj, field.attr))
    stream.id = idx
    stream.data = data
    stream.size = len(data)
    buf.extend(stream.write())
  return bytes(buf)

def layout_differences(layout: list[tuple[int, bytes | None]] | None, fields: list[H5_Field]) -> str:
  """How a layout differs from the fields': the fields kept as they came (16 at most listed) and the fields it
  doesn't have. Empty for 1.60's layout."""
  if layout is None:
    return ""
  kept = [f"{idx} (size {len(data)})" for idx, data in layout if data is not None]
  if len(kept) > 16:
    kept = kept[:16] + [f"{len(kept) - 16} more"]
  have = {idx for idx, _ in layout}
  missing = [str(field.idx) for field in fields if field.idx not in have]
  parts = []
  if kept:
    parts.append("kept as sent: " + ", ".join(kept))
  if missing:
    parts.append("missing: " + ", ".join(missing))
  return "; ".join(parts)

def read_bool(data: bytes) -> bool:
  return data[0] != 0

def write_bool(value: bool) -> bytes:
  return bytes([1 if value else 0])

def read_i32(data: bytes) -> int:
  return c_int32(read_u32(data)).value

def write_i32(value: int) -> bytes:
  return write_u32(c_uint32(value).value)

def read_string(data: bytes) -> str:
  return data.decode(GAME_ENCODING)

def write_string(value: str) -> bytes:
  return value.encode(GAME_ENCODING, errors="replace")

class H5_PlayerInfo:
  def __init__(self, buf: bytes = None):
    self.key: int = None
    """Id written before the entry in `RoomInfo.player_infos`; `None` numbers the entries from 0."""
    self.username = ""
    """idx = 2; empty for player slots nobody took yet"""
    self.ext_ip = H5_IP()
    """idx = 3; external (public) address"""
    self.local_port = 0
    """idx = 4; local (private) port"""
    self.local_ip = H5_LocalIP()
    """idx = 4; local (private) IPv4"""
    self.int38 = 0
    """idx = 5; unknown value"""
    self.layout: list[tuple[int, bytes | None]] = None
    """The fields as they came (see `read_fields`), `None` for 1.60's layout."""
    if buf is not None:
      self.layout = read_fields(self, buf, PLAYER_FIELDS, "PlayerInfo")

  @property
  def local_port_ip(self):
    """idx = 4: the local port and IPv4."""
    return self.local_port, self.local_ip

  @local_port_ip.setter
  def local_port_ip(self, value):
    self.local_port, self.local_ip = value

  @staticmethod
  def read_port_ipv4(buf: bytes):
    """Reads port and IPv4 address; compare with `read_ipv4`"""
    # port - idx 2
    field = H5_Stream(buf)
    if field.id == 2 and field.size == 2:
      port = read_u16(field.data)
    else:
      raise ValueError(f"PlayerInfo: missing/invalid field - PortIP.Port ({field.id})")
    # ip - idx 3
    field = H5_Stream(buf[len(field):])
    if field.id == 3 and field.size == 16:
      ip = H5_LocalIP(field.data)
    else:
      raise ValueError(f"PlayerInfo: missing/invalid field - PortIP.IP ({field.id})")
    return port, ip

  @staticmethod
  def write_port_ipv4(port: int, ip: H5_LocalIP):
    """Writes port and IPv4 address; compare with `write_ipv4`"""
    buf = bytearray()
    field = H5_Stream()

    field.id = 2
    field.data = write_u16(port)
    field.size = len(field.data)
    buf.extend(field.write())

    field.id = 3
    field.data = ip.serialize()
    field.size = len(field.data)
    buf.extend(field.write())
    return bytes(buf)

  def serialize(self):
    """Returns a serialized buffer."""
    return write_fields(self, self.layout, PLAYER_FIELDS)

PLAYER_FIELDS = [
  H5_Field(2, "username", "Username", None, read_string, write_string),
  H5_Field(3, "ext_ip", "ExtIP", 18,
    lambda data: H5_Serializer.read_ipv4(data, False), lambda value: H5_Serializer.write_ipv4(value), required=True),
  H5_Field(4, "local_port_ip", "LocalPortIP", None,
    H5_PlayerInfo.read_port_ipv4, lambda value: H5_PlayerInfo.write_port_ipv4(*value), required=True),
  H5_Field(5, "int38", "Int38", 4, read_u32, write_u32),
]
"""The fields of 1.60's `PlayerInfo`, in its order."""

class H5_RoomInfo:
  """Data from `group.Room.group_info` buffer (HoMM5)."""
  def __init__(self, buf: bytes = None):
    self.group_id: int = -1
    """idx = 2"""
    self.lobby_sv_id: int = -1
    """idx = 3"""
    self.host_ip = H5_IP()
    """idx = 4"""
    self.host_logic_init: bool = False
    """idx = 6"""
    self.group_name: str = ""
    """idx = 7"""
    self.password: str = ""
    """idx = 8"""
    self.is_pwd_protected: bool = False
    """idx = 9"""
    self.ghost_mode: bool = False
    """idx = 10"""
    self.quick_combat: bool = False
    """idx = 11"""
    self.fast_combat_turns: bool = False
    """idx = 12"""
    self.time_limit: int = 0
    """idx = 13"""
    self.difficulty_id: int = 0
    """idx = 14"""
    self.map_desc: str = ""
    """idx = 15; map descriptor path (`AdvMapDesc`)"""
    self.teams: list[int] = []
    """idx = 19"""
    self.max_players: int = 0
    """idx = 20"""
    self.map_size: int = 0
    """idx = 21"""
    self.player_infos: list[H5_PlayerInfo] = []
    """idx = 22"""
    self.game_version: int = 0
    """idx = 23"""
    self.is_saved: bool = False
    """idx = 24"""
    self.guid: str = ""
    """idx = 25"""
    self.ubi_send_results_wait: bool = False
    """idx = 26"""
    self.options: dict[str, tuple[int, str]] = {}
    """idx = 27"""
    self.is_arena: bool = False
    """idx = 28"""
    self.host_checksum: int = 0
    """idx = 29"""
    self.adventure_type: int = 0
    """idx = 30"""
    self.map_desc_tag: str = ""
    """idx = 31"""
    self.map_goal: str = ""
    """idx = 32"""
    self.arena_map_name: str = ""
    """idx = 33"""
    self.int104: int = 0
    """idx = 37"""
    self.int108: int = 0
    """idx = 38"""
    self.combat_turn_speed: int = 0
    """idx = 39"""
    self.flag110: bool = False
    """idx = 40"""
    self.layout: list[tuple[int, bytes | None]] = None
    """The fields as they came (see `read_fields`), `None` for 1.60's layout."""
    if buf is not None:
      self.layout = read_fields(self, buf, ROOM_FIELDS, "RoomInfo")

  @staticmethod
  def read_nested_string(buf: bytes):
    """Reads a string nested inside idx 2 field."""
    # nested field
    inner_field = H5_Stream(buf)
    idx = 2
    if inner_field.id == idx and inner_field.size > 0:
      return inner_field.data.decode(GAME_ENCODING)
    elif inner_field.size == 0:
      return ""
    else:
      raise ValueError(f"RoomInfo: missing/invalid field - String.Value ({idx})")

  @staticmethod
  def read_uints(buf: bytes):
    """Reads a list of LE uint32s."""
    # nested field - idx 1 (entry count)
    field = H5_Stream(buf)
    if field.id == 1 and field.size == 4:
      # nested field - idx 2 (entries)
      field = H5_Stream(buf[len(field):])
      if field.id == 2:
        return read_as_u32_list(field.data)
      else:
        raise ValueError(f"RoomInfo: missing/invalid field - IDs.Entries (2)")
    else:
      raise ValueError(f"RoomInfo: missing/invalid field - IDs.Count (1)")

  @staticmethod
  def read_player_infos(buf: bytes):
    """Reads a list of player info structs."""
    # nested field - idx 3 (entry count)
    field = H5_Stream(buf)
    if field.id == 3 and field.size == 4:
      count = read_u32(field.data)
      pos = len(field)
      field = H5_Stream(buf[pos:])
      if field.id == 4 and field.size == 4:
        mode_setting_2 = read_u32(field.data)
        # idx 1 - sizes
        pos += len(field)
        sizes: list[int] = []
        for _ in range(count):
          field = H5_Stream(buf[pos:])
          if field.id == 1 and field.size == 4:
            sizes.append(read_u32(field.data))
          else:
            raise ValueError(f"RoomInfo: missing/invalid field - PlayerInfos.Count (1)")  
          pos += len(field)

        player_infos: list[H5_PlayerInfo] = []
        for i in range(count):
          field = H5_Stream(buf[pos:])
          if field.id == 2:
            info = H5_PlayerInfo(field.data)
            info.key = sizes[i]
            player_infos.append(info)
          else:
            raise ValueError(f"RoomInfo: missing/invalid field - PlayerInfos.PlayerInfo (2)")  
          pos += len(field)
        return player_infos
      else:
        raise ValueError(f"RoomInfo: missing/invalid field - PlayerInfos.ModeSetting2 (4)")  
    else:
      raise ValueError(f"RoomInfo: missing/invalid field - PlayerInfos.ModeSetting1 (3)")

  @staticmethod
  def read_options(buf: bytes):
    """Reads options (`map<string, int32, string>`)."""
    # nested field - idx 3 (entry count)
    field = H5_Stream(buf)
    if field.id == 3 and field.size == 4:
      count = read_u32(field.data)
      pos = len(field)
      field = H5_Stream(buf[pos:])
      if field.id == 4 and field.size == 4:
        # unknown value, 0xD (13)
        mode_setting_2 = read_u32(field.data)
        # a number of idx 1 key (string) followed by a list of (idx 2 + idx 3) value tuples (int32, utf16 string)
        options: dict[str, tuple[int, str]] = {}
        keys: list[str] = []
        pos += len(field)
        for _ in range(count):
          field = H5_Stream(buf[pos:])
          if field.id == 1:
            keys.append(field.data.decode(GAME_ENCODING))
          else:
            raise ValueError(f"RoomInfo: missing/invalid field - Options.Key (1)")
          pos += len(field)
        
        values: list[tuple[int, str]] = []
        for _ in range(count):
          field = H5_Stream(buf[pos:])
          # nested field - idx 2
          if field.id == 2:
            int_value, str_value = H5_RoomInfo.read_option_values(field.data)
          else:
            raise ValueError(f"RoomInfo: missing/invalid field - Options.IntValue (2)")
          values.append((int_value, str_value))
          pos += len(field)

        for idx in range(count):
          options[keys[idx]] = (values[idx][0], values[idx][1])

        return options
      else:
        raise ValueError(f"RoomInfo: missing/invalid field - PlayerInfos.ModeSetting2 (4)")  
    else:
      raise ValueError(f"RoomInfo: missing/invalid field - PlayerInfos.ModeSetting1 (3)")

  @staticmethod
  def read_option_values(buf: bytes):
    """Reads `<int, str>` value pair; the string value is in UTF-16."""
    pos = 0
    field = H5_Stream(buf)
    if field.id == 2:
      int_value = read_u32(field.data)
    else:
      raise ValueError(f"RoomInfo: missing/invalid field - Options.IntValue (2)")
    pos += len(field)
    field = H5_Stream(buf[pos:])
    if field.id == 3:
      str_value = field.data.decode("utf-16-le")
    else:
      raise ValueError(f"RoomInfo: missing/invalid field - Options.StringValue (3)")
    return int_value, str_value

  @staticmethod
  def write_nested_string(string: str):
    """Writes a string as nested inside idx 2 field."""
    field = H5_Stream()
    field.id = 2
    field.data = string.encode(GAME_ENCODING, errors="replace")
    field.size = len(field.data)
    return field.write()

  @staticmethod
  def write_uints(lst: list[int]):
    """Writes a list of ints as LE uint32s."""
    buf = bytearray()
    # count - idx 1
    field = H5_Stream()
    field.id = 1
    field.data = write_u32(len(lst))
    field.size = len(field.data)
    buf.extend(field.write())

    # data - idx 2
    field.id = 2
    field.data = write_u32_list(lst)
    field.size = len(field.data)
    buf.extend(field.write())
    return bytes(buf)

  @staticmethod
  def write_player_infos(infos: list[H5_PlayerInfo]):
    """Writes a list of player info structs."""
    buf = bytearray()
    # count - idx 3
    field = H5_Stream()
    field.id = 3
    field.data = write_u32(len(infos))
    field.size = len(field.data)
    buf.extend(field.write())
    # unknown value 13 - idx 4
    field.id = 4
    field.data = write_u32(13)
    field.size = len(field.data)
    buf.extend(field.write())
    data = bytearray()
    some_id = 0
    for info in infos:
      # info entries - idx 2
      field.id = 2
      field.data = info.serialize()
      field.size = len(field.data)
      data.extend(field.write())
      # either ids (starting at 0), positions or zeroes - idx 1
      field.id = 1
      field.data = write_u32(some_id if info.key is None else info.key)
      field.size = len(field.data)
      buf.extend(field.write())
      some_id += 1
    
    buf.extend(data)
    return bytes(buf)

  @staticmethod
  def write_options(options: dict[str, tuple[int, str]]):
    """Writes options (`map<string, int32, string>`)."""
    buf = bytearray()
    # count - idx 3
    field = H5_Stream()
    field.id = 3
    field.data = write_u32(len(options))
    field.size = len(field.data)
    buf.extend(field.write())
    # unknown value 13 - idx 4
    field.id = 4
    field.data = write_u32(13)
    field.size = len(field.data)
    buf.extend(field.write())
    data = bytearray()
    for key in options:
      # keys - idx 1
      field.id = 1
      field.data = key.encode(GAME_ENCODING, errors="replace")
      field.size = len(field.data)
      buf.extend(field.write())
      # values - idx 2
      field.id = 2
      field.data = H5_RoomInfo.write_option_values(options[key])
      field.size = len(field.data)
      data.extend(field.write())

    buf.extend(data)
    return bytes(buf)

  @staticmethod
  def write_option_values(values: tuple[int, str]):
    """Writes `<int, str>` value pair; the string value is in UTF-16."""
    buf = bytearray()
    field = H5_Stream()

    field.id = 2
    field.data = write_u32(values[0])
    field.size = len(field.data)
    buf.extend(field.write())

    field.id = 3
    field.data = values[1].encode("utf-16-le")
    field.size = len(field.data)
    buf.extend(field.write())

    return bytes(buf)
  
  def serialize(self):
    """Returns a serialized buffer."""
    return write_fields(self, self.layout, ROOM_FIELDS)

  def differences(self):
    """How the room info's fields differ from 1.60's: the fields kept as they came, and 1.60's fields it
    doesn't have. Empty for a room info laid out like 1.60's."""
    return layout_differences(self.layout, ROOM_FIELDS)

def read_utf16(data: bytes) -> str:
  return data.decode("utf-16-le")

def write_utf16(value: str) -> bytes:
  return value.encode("utf-16-le")

def read_guid(data: bytes) -> str:
  return f"{data[:4].hex()}-{data[4:6].hex()}-{data[6:8].hex()}-{data[8:10].hex()}-{data[10:].hex()}"

ROOM_FIELDS = [
  H5_Field(2, "group_id", "GroupID", 4, read_i32, write_i32, required=True),
  H5_Field(3, "lobby_sv_id", "LobbySrvID", 4, read_i32, write_i32, required=True),
  H5_Field(4, "host_ip", "host IP", 18,
    lambda data: H5_Serializer.read_ipv4(data, True), lambda value: H5_Serializer.write_ipv4(value), required=True),
  H5_Field(6, "host_logic_init", "bHostLogicInitialized", 1, read_bool, write_bool),
  H5_Field(7, "group_name", "GroupName", None, read_utf16, write_utf16),
  H5_Field(8, "password", "Password", None, read_utf16, write_utf16),
  H5_Field(9, "is_pwd_protected", "IsPasswordProtected", 1, read_bool, write_bool),
  H5_Field(10, "ghost_mode", "GhostMode", 1, read_bool, write_bool),
  H5_Field(11, "quick_combat", "QuickCombat", 1, read_bool, write_bool),
  H5_Field(12, "fast_combat_turns", "FastCombatTurns", 1, read_bool, write_bool),
  H5_Field(13, "time_limit", "TimeLimit", 4, read_i32, write_i32),
  H5_Field(14, "difficulty_id", "DifficultyID", 4, read_u32, write_u32),
  H5_Field(15, "map_desc", "MapDescriptor", None, H5_RoomInfo.read_nested_string, H5_RoomInfo.write_nested_string),
  H5_Field(19, "teams", "Teams", None, H5_RoomInfo.read_uints, H5_RoomInfo.write_uints),
  H5_Field(20, "max_players", "MaxPlayers", 4, read_u32, write_u32),
  H5_Field(21, "map_size", "MapSize", 4, read_u32, write_u32),
  H5_Field(22, "player_infos", "PlayerInfos", None,
    H5_RoomInfo.read_player_infos, H5_RoomInfo.write_player_infos, required=True),
  H5_Field(23, "game_version", "GameVersion", 4, read_u32, write_u32),
  H5_Field(24, "is_saved", "IsSaved", 1, read_bool, write_bool),
  H5_Field(25, "guid", "GUID", 16, read_guid, lambda value: UUID(value).bytes),
  H5_Field(26, "ubi_send_results_wait", "SendResults", 1, read_bool, write_bool),
  H5_Field(27, "options", "Options", None, H5_RoomInfo.read_options, H5_RoomInfo.write_options),
  H5_Field(28, "is_arena", "IsArena", 1, read_bool, write_bool),
  H5_Field(29, "host_checksum", "HostChecksum", 4, read_u32, write_u32),
  H5_Field(30, "adventure_type", "AdventureType", 4, read_u32, write_u32),
  H5_Field(31, "map_desc_tag", "MapDescTag", None, H5_RoomInfo.read_nested_string, H5_RoomInfo.write_nested_string),
  H5_Field(32, "map_goal", "MapGoal", None, read_string, write_string),
  H5_Field(33, "arena_map_name", "ArenaMapName", None, read_string, write_string),
  H5_Field(37, "int104", "Int104", 4, read_u32, write_u32),
  H5_Field(38, "int108", "Int108", 4, read_u32, write_u32),
  H5_Field(39, "combat_turn_speed", "CombatTurnSpeed", 4, read_u32, write_u32),
  H5_Field(40, "flag110", "Flag110", 1, read_bool, write_bool),
]
"""The fields of 1.60's `CRoomInfo`, in its order. The lobby sets the ids and changes the addresses, the host's
and the players'."""

class H5_Room:
  """Full room information matchmaking object, includes game-specific data."""
  def __init__(self, room: Room, info: H5_RoomInfo):
    self.gs_room = room
    self.room_info = info
    self.match_id: int = None
    """Rated match being played in the room."""
    self.creator = None
    """The lobby connection that created the room; it joins the room next."""

class H5_Serializer:
  """Equivalent to the original `CStructureSaver` serializer (HoMM5)."""
  def __init__(self):
    self.streams: dict[str, H5_Stream] = {}

  def serialize_roominfo(self, room_info: H5_RoomInfo):
    """Serializes `CRoomInfo` structure into a `group.Room.group_info` buffer."""
    return self.serialize_struct(room_info.serialize())

  def serialize_playerinfo(self, player_info: H5_PlayerInfo):
    """Serializes `PlayerInfo` structure into a lobby member data buffer (`LOBBY_MSG.SET_PLAYER_INFO`)."""
    return self.serialize_struct(player_info.serialize())

  def serialize_struct(self, data: bytes):
    """Wraps serialized structure fields into the file streams."""
    # stream 0 - empty
    struct_stream = H5_Stream()
    struct_stream.id = H5_STREAM_TYPE.STRUCTURE.value
    struct_stream.size_4b = False
    struct_stream.size = 0
    
    # stream 1 - room info
    data_stream = H5_Stream()
    data_stream.id = H5_STREAM_TYPE.COMPRESSED_DATA.value
    data_stream.data = data
    data_stream.size = len(data_stream.data)
    # the limit for 1B size is 127
    data_stream.size_4b = data_stream.size > 127

    # stream 2 - empty
    raw_data_stream = H5_Stream()
    raw_data_stream.id = H5_STREAM_TYPE.RAW_DATA.value
    raw_data_stream.size_4b = False
    raw_data_stream.size = 0

    # stream 4 - serialization mode (4)
    mode_stream = H5_Stream()
    mode_stream.id = H5_STREAM_TYPE.SERIALIZATION_MODE.value
    mode_stream.size_4b = False
    mode_stream.size = 4
    mode_stream.data = write_u32(4)

    # stream 5 - empty
    table_stream = H5_Stream()
    table_stream.id = H5_STREAM_TYPE.LOOKUP_TABLE.value
    table_stream.size_4b = False
    table_stream.size = 0

    self.streams = {
      H5_STREAM_TYPE.STRUCTURE.name: struct_stream,
      H5_STREAM_TYPE.COMPRESSED_DATA.name: data_stream,
      H5_STREAM_TYPE.RAW_DATA.name: raw_data_stream,
      H5_STREAM_TYPE.SERIALIZATION_MODE.name: mode_stream,
      H5_STREAM_TYPE.LOOKUP_TABLE.name: table_stream
    }

    buf = bytearray(self.streams[H5_STREAM_TYPE.SERIALIZATION_MODE.name].write())
    buf.extend(self.streams[H5_STREAM_TYPE.COMPRESSED_DATA.name].write())
    buf.extend(self.streams[H5_STREAM_TYPE.STRUCTURE.name].write())
    buf.extend(self.streams[H5_STREAM_TYPE.RAW_DATA.name].write())
    buf.extend(self.streams[H5_STREAM_TYPE.LOOKUP_TABLE.name].write())
    return bytes(buf)

  def deserialize_playerinfo(self, buf: bytes) -> H5_PlayerInfo:
    """Reads a `PlayerInfo` structure from a serialized buffer (`LOBBY_MSG.SET_PLAYER_INFO`)."""
    return H5_PlayerInfo(self.data_stream(buf).data)

  def data_stream(self, buf: bytes) -> H5_Stream:
    """Reads the streams of a serialized buffer, returns its data stream."""
    size = len(buf)
    pos = 0
    while pos < size:
      stream = H5_Stream(buf)
      bts_read = len(stream)
      pos += bts_read
      self.streams[H5_STREAM_TYPE(stream.id).name] = stream
      buf = buf[bts_read:]
    data_stream = self.streams.get(H5_STREAM_TYPE.COMPRESSED_DATA.name)
    if data_stream is None:
      raise ValueError("Data stream missing in the buffer.")
    return data_stream

  def deserialize_roominfo(self, buf: bytes) -> H5_RoomInfo:
    """Reads `CRoomInfo` structure from a serialized `group.Room.group_info` buffer."""
    size = len(buf)
    pos = 0
    # streams
    while (pos < size):
      stream = H5_Stream(buf)
      bts_read = len(stream)
      pos += bts_read
      self.streams[H5_STREAM_TYPE(stream.id).name] = stream
      buf = buf[bts_read:]

    data_stream = self.streams[H5_STREAM_TYPE.COMPRESSED_DATA.name]
    if data_stream is None:
      raise ValueError("Data stream missing in RoomInfo buffer.")

    room_info = H5_RoomInfo(data_stream.data)
    return room_info

  @staticmethod
  def read_ipv4(buf: bytes, room_info: bool):
    """Reads `sockaddr_in` address encoded on 16B. Shared between `RoomInfo` and `PlayerInfo`."""
    # nested field - idx 2
    field = H5_Stream(buf)
    if field.id == 2 and field.size == 16:
      return H5_IP(field.data)
    else:
      cl_name = "RoomInfo" if room_info else "PlayerInfo"
      raise ValueError(f"{cl_name}: missing/invalid field - HostIP (2)")

  @staticmethod
  def write_ipv4(ip: H5_IP):
    """Writes `sockaddr_in` address structure encoded on 16B. Shared between `RoomInfo` and `PlayerInfo`."""
    # nested field - idx 2
    field = H5_Stream()
    field.id = 2
    field.data = ip.serialize()
    field.size = len(field.data)
    return field.write()
