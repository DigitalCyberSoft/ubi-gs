"""ladderquery proxy module data: player ratings and statistics (code 1281)."""
import os, sys
root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.append(root_dir)
import accounts, client

QUERY = '1281'
"""Module data code of a ladder query."""

RACES = ['HEAVEN', 'PRESERVE', 'ACADEMY', 'DUNGEON', 'NECROMANCY', 'INFERNO', 'DWARVES']

FIELDS = ['RATING', 'GAMES_PLAYED', 'WINS', 'LOSSES', 'MAX_WINS_STREAK', 'MAX_LOSSES_STREAK', 'CUR_WINS_STREAK',
  'CUR_LOSSES_STREAK', 'TOT_TIME_PLAYED', 'TOT_HEROES_HIRED', 'TOT_HEROES_LOST', 'TOT_HEROES_DEFEATED'] + \
  [f'{kind}_{race}' for kind in ['W', 'L', 'H', 'G'] for race in RACES] + ['AVERAGE_HERO_LEVEL', 'DISCONNECTIONS']
"""The fields the game reads from a ladder row (table 0x00b1b1e0)."""

START_RATING = 1500

def defaults():
  stats = {field: 0 for field in FIELDS}
  stats['RATING'] = START_RATING
  return stats

def stats(account: accounts.Account, ladder: str):
  """All fields for the account, stored values over the defaults."""
  result = defaults()
  result.update(accounts.ladder_stats(account, ladder))
  return result

def ladder_key(game: str, ladder_id: str):
  return f'{game}/{ladder_id}'

def player(name: str, clt: client.TcpClient):
  """The account a query is about: the asking game's own typed name, or another player's account name."""
  return accounts.session(name, clt.addr[0]) or accounts.find(name)

def query(clt: client.TcpClient, body: list):
  """Answers a query `['1', [reqId, game, ladderId, '0', [type, args], [filters, sort, fields]]]`.

  Returns the result list of the reply: success ['38', ['1', [reqId, '1', columns, rows]]] (parsed at 0x00432b50;
  the game reads row 0 only), or failure ['39', [reason]], after which the game shows N/A. The game takes the
  first field of the result for the id of the request it answers (0x004325c0) and ignores a reply to a request
  it isn't waiting for, so the Profile screen, whose query is never a session's first, waited forever while
  that field was always 1."""
  request = body[1]
  request_id, game, ladder_id, (query_type, args) = request[0], request[1], request[2], request[4]
  # pivot queries (1, 7): [user, count]
  if query_type not in ('1', '7') or not args:
    return ['39', ['0']]
  account = player(args[0], clt)
  if account is None:
    return ['39', ['0']]
  values = stats(account, ladder_key(game, ladder_id))
  columns = [[field, field] for field in FIELDS]
  row = [str(values[field]) for field in FIELDS]
  return ['38', ['1', [request_id, '1', columns, [row]]]]
