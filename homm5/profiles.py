"""persistantdata proxy module data: the Profile screen's stored data (codes 1025 get, 1026 set)."""
import os, sys
root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.append(root_dir)
import accounts, client

GET = '1025'
SET = '1026'

MAX_SIZE = 64 * 1024
"""Largest profile blob stored."""

NO_DATA = '1'
"""Failure reason: nothing stored."""

def player(name: str, clt: client.TcpClient):
  """The account a request is about: the asking game's own typed name, or another player's account name."""
  return accounts.session(name, clt.addr[0]) or accounts.find(name)

def get_data(clt: client.TcpClient, body: list):
  """[game, '0', user, '0', 'PUBLIC'] -> ['38', [blob, length]] (0x0042b2d0), or ['39', [reason]]."""
  game, user = body[0], body[2]
  account = player(user, clt)
  data = accounts.profile(account, game) if account else None
  if data is None:
    return ['39', [NO_DATA]]
  return ['38', [data, str(len(data))]]

def set_data(clt: client.TcpClient, body: list):
  """[game, '0', own user, '0', 'PUBLIC', blob, length] -> ['38', []] (0x0042b1b0), or ['39', [reason]].
  Only the player's own data is written."""
  game, user, data = body[0], body[2], bytes(body[5])
  account = accounts.session(user, clt.addr[0])
  if account is None or len(data) > MAX_SIZE:
    return ['39', ['0']]
  accounts.set_profile(account, game, data)
  return ['38', []]
