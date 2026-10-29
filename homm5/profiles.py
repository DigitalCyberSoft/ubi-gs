"""persistantdata proxy module data: the Profile screen's stored data (codes 1025 get, 1026 set).

The data is what the game's structure saver (CStructureSaver) writes for the profile (NUI::CMPProfileSaver,
0x009d81b0; read back at 0x009f6770): sections, each `[id][length][data]`, where the length is a byte holding
length * 2, or, when length * 2 doesn't fit a byte, 4 bytes (little-endian) holding length * 2 | 1:
- 4: the saver's version, an int32, 4;
- 1: the data, chunks in the same format:
  - 1: the profile (0x009d8440), with
    - 2: the avatar, an int32 (0x1003c by default, 0x008d0780),
    - 4: the "Additional Information" text, UTF-16LE, no terminator, at most 255 characters (its edit box's
      MaxLength);
- 0, 2 and 5: the saver's object table, object data and type map, empty for the profile.

The rest of the screen (ratings, games, races) comes from the ladder. The game saves when the player changes
the text and leaves the screen (OK or Back, 0x009f3650): only its own profile, and only from the lobby.
"""
import os, struct, sys
root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.append(root_dir)
import accounts, client

GET = '1025'
SET = '1026'

MAX_SIZE = 1024
"""Largest profile data accepted; the game's own is at most 543 bytes."""

MAX_TEXT = 255
"""Most characters in the text (the edit box's MaxLength)."""

VERSION = 4
"""The structure saver's version (0x010cfa58)."""

DEFAULT_AVATAR = 0x1003c
"""The avatar of a new profile."""

NO_DATA = '1'
"""Failure reason: no such player."""

def _chunk(chunk_id: int, data: bytes) -> bytes:
  n = len(data) * 2
  length = bytes([n]) if n < 0x100 else struct.pack('<I', n | 1)
  return bytes([chunk_id]) + length + data

def _chunks(data: bytes) -> list[tuple[int, bytes]]:
  """The chunks of `data`; ValueError if it isn't a sequence of whole chunks."""
  chunks, pos = [], 0
  while pos < len(data):
    if pos + 2 > len(data):
      raise ValueError('truncated chunk header')
    chunk_id, n = data[pos], data[pos + 1]
    pos += 2
    if n & 1:
      if pos + 3 > len(data):
        raise ValueError('truncated chunk length')
      n = struct.unpack('<I', data[pos - 1:pos + 3])[0]
      pos += 3
    n >>= 1
    if pos + n > len(data):
      raise ValueError('truncated chunk')
    chunks.append((chunk_id, data[pos:pos + n]))
    pos += n
  return chunks

def _unique(chunks: list[tuple[int, bytes]], allowed: set[int]) -> dict[int, bytes]:
  found = {}
  for chunk_id, data in chunks:
    if chunk_id not in allowed or chunk_id in found:
      raise ValueError(f'unexpected chunk {chunk_id}')
    found[chunk_id] = data
  return found

def encode(avatar: int = DEFAULT_AVATAR, text: str = '') -> bytes:
  """Profile data as the game writes it."""
  profile = _chunk(2, struct.pack('<i', avatar)) + _chunk(4, text.encode('utf-16-le', errors='surrogatepass'))
  return (_chunk(4, struct.pack('<i', VERSION)) + _chunk(1, _chunk(1, profile)) +
    _chunk(0, b'') + _chunk(2, b'') + _chunk(5, b''))

def decode(data: bytes) -> tuple[int, str]:
  """(avatar, text) of profile data; ValueError unless it is a profile and nothing else. A missing avatar or
  text reads as the default, as in the game."""
  sections = _unique(_chunks(data), {0, 1, 2, 4, 5})
  if sections.get(4) != struct.pack('<i', VERSION):
    raise ValueError('not version 4')
  if any(sections.get(i, b'') for i in (0, 2, 5)):
    raise ValueError('objects in a profile')
  outer = _unique(_chunks(sections.get(1, b'')), {1})
  fields = _unique(_chunks(outer.get(1, b'')), {2, 4})
  avatar, text = DEFAULT_AVATAR, b''
  if 2 in fields:
    if len(fields[2]) != 4:
      raise ValueError('avatar size')
    avatar = struct.unpack('<i', fields[2])[0]
  if 4 in fields:
    text = fields[4]
    if len(text) % 2 or len(text) > MAX_TEXT * 2:
      raise ValueError('text size')
  return avatar, text.decode('utf-16-le', errors='surrogatepass')

DEFAULT = encode()
"""A new player's profile: the default avatar, no text."""

def player(name: str, clt: client.TcpClient):
  """The account a request is about: the asking game's own typed name, or another player's account name."""
  return accounts.session(name, clt.addr[0]) or accounts.find(name)

def get_data(clt: client.TcpClient, body: list):
  """[game, '0', user, '0', 'PUBLIC'] -> ['38', [data, length]] (0x0042b2d0), or ['39', [reason]] for no such
  player. A player who never saved gets the default profile: the failure shows an error instead of the screen."""
  game, user = body[0], body[2]
  account = player(user, clt)
  if account is None:
    return ['39', [NO_DATA]]
  data = accounts.profile(account, game) or DEFAULT
  return ['38', [data, str(len(data))]]

def set_data(clt: client.TcpClient, body: list):
  """[game, '0', own user, '0', 'PUBLIC', data, length] -> ['38', []] (0x0042b1b0), or ['39', [reason]].
  Only the player's own data is written, and only a profile: other players' games load it, so it is
  stored as this server writes it again."""
  game, user, data = body[0], body[2], bytes(body[5])
  account = accounts.session(user, clt.addr[0])
  if account is None or len(data) > MAX_SIZE:
    return ['39', ['0']]
  try:
    avatar, text = decode(data)
  except ValueError:
    return ['39', ['0']]
  accounts.set_profile(account, game, encode(avatar, text))
  return ['38', []]
