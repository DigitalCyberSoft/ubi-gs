"""Player accounts, friends, ladder stats and profile data in SQLite.

The database is a file next to this module (`ubigs.db`), or UBIGS_DB. `UBIGS_DB=:memory:` keeps everything in memory
only: it is shared by the services running in one process and lost when that process ends.
Passwords are stored as salted scrypt hashes only.
"""
import hashlib, os, re, secrets, sqlite3, threading, time
from dataclasses import dataclass
from enum import Enum
from utils import cap

DEFAULT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'ubigs.db')
"""Database file used when UBIGS_DB isn't set."""

MEMORY = ':memory:'

NAME_SIZE = 11
"""Longest account name in UTF-8 bytes, suffix included: the game's PLAYERINFO name buffer is 12 bytes (0x0042a990)."""

SCHEMA_VERSION = 1

SCRYPT = {'n': 2 ** 14, 'r': 8, 'p': 1, 'dklen': 32}
"""scrypt cost parameters."""

class LOGIN_ERROR(Enum):
  """Router `LOGIN` failure reasons, as the game names them (0x00b03490)."""
  USERNAME_MALFORMED = 2
  INVALID_PASSWORD = 15
  PASSWORD_MALFORMED = 11
  DATABASE_FAILED = 100

class LoginError(Exception):
  """Refused login, with the reason the game is sent."""
  def __init__(self, reason: LOGIN_ERROR):
    super().__init__(reason.name)
    self.reason = reason

@dataclass(frozen=True)
class Account:
  id: int
  name: str

_lock = threading.RLock()
"""Serialises use of the connection within this process."""

_db: sqlite3.Connection | None = None
_path: str | None = None

def configure(path: str | None = None):
  """Selects the database (a file path or `:memory:`); None means UBIGS_DB or the default file. Closes an open one."""
  global _db, _path
  with _lock:
    if _db is not None:
      _db.close()
      _db = None
    _path = path

def db_path() -> str:
  return _path or os.environ.get('UBIGS_DB') or DEFAULT_PATH

def _open(path: str) -> sqlite3.Connection:
  conn = sqlite3.connect(path, timeout=30, isolation_level=None, check_same_thread=False)
  try:
    if path != MEMORY:
      conn.execute('PRAGMA journal_mode=WAL')
    conn.execute('PRAGMA foreign_keys=ON')
    _migrate(conn)
  except sqlite3.DatabaseError:
    conn.close()
    raise
  return conn

def _migrate(conn: sqlite3.Connection):
  version = conn.execute('PRAGMA user_version').fetchone()[0]
  if version > SCHEMA_VERSION:
    raise sqlite3.DatabaseError(f'database schema {version} is newer than this server ({SCHEMA_VERSION})')
  if version < 1:
    conn.executescript('''
      BEGIN;
      CREATE TABLE IF NOT EXISTS accounts (
        id INTEGER PRIMARY KEY,
        name TEXT NOT NULL UNIQUE COLLATE NOCASE,
        hash BLOB NOT NULL,
        salt BLOB NOT NULL,
        created INTEGER NOT NULL
      );
      CREATE TABLE IF NOT EXISTS sessions (
        typed TEXT NOT NULL COLLATE NOCASE,
        ip TEXT NOT NULL,
        account INTEGER NOT NULL REFERENCES accounts(id),
        updated INTEGER NOT NULL,
        PRIMARY KEY (typed, ip)
      );
      CREATE TABLE IF NOT EXISTS friends (
        account INTEGER NOT NULL REFERENCES accounts(id),
        friend INTEGER NOT NULL REFERENCES accounts(id),
        PRIMARY KEY (account, friend)
      );
      CREATE TABLE IF NOT EXISTS ladder (
        account INTEGER NOT NULL REFERENCES accounts(id),
        game TEXT NOT NULL,
        field TEXT NOT NULL,
        value INTEGER NOT NULL,
        PRIMARY KEY (account, game, field)
      );
      CREATE TABLE IF NOT EXISTS profiles (
        account INTEGER NOT NULL REFERENCES accounts(id),
        game TEXT NOT NULL,
        data BLOB NOT NULL,
        PRIMARY KEY (account, game)
      );
      PRAGMA user_version = 1;
      COMMIT;
    ''')
  # live players per lobby, which the lobby service starts over at every start: not a schema change (the Go port
  # opens the same file and has no use for it)
  conn.execute('CREATE TABLE IF NOT EXISTS lobby_members (lobby INTEGER PRIMARY KEY, members INTEGER NOT NULL)')
  # who is in each room, its host and who left it lately, for the IRC service: also restarted by the lobby service
  conn.execute('CREATE TABLE IF NOT EXISTS room_members (room INTEGER NOT NULL, name TEXT NOT NULL, '
    'typed TEXT NOT NULL, host INTEGER NOT NULL, gone REAL)')

def db() -> sqlite3.Connection:
  """The open database. An unreadable file is moved aside (`.corrupt-<time>`) and a new one started."""
  global _db
  with _lock:
    if _db is None:
      path = db_path()
      try:
        _db = _open(path)
      except sqlite3.DatabaseError as err:
        if path == MEMORY:
          raise
        aside = f'{path}.corrupt-{int(time.time())}'
        print(f'Accounts database {path} is unreadable ({err}), moved to {aside}; starting a new one')
        for suffix in ['', '-wal', '-shm']:
          if os.path.exists(path + suffix):
            os.replace(path + suffix, aside + suffix)
        _db = _open(path)
    return _db

class _transaction:
  """Write transaction, also exclusive against other processes on the same file."""
  def __enter__(self):
    _lock.acquire()
    try:
      self.conn = db()
      self.conn.execute('BEGIN IMMEDIATE')
    except BaseException:
      _lock.release()
      raise
    return self.conn

  def __exit__(self, kind, value, tb):
    try:
      self.conn.execute('ROLLBACK' if kind else 'COMMIT')
    finally:
      _lock.release()

def _query(sql: str, args=()):
  with _lock:
    return db().execute(sql, args).fetchall()

# names

def cut(text: str, size: int) -> str:
  """`text` cut to at most `size` UTF-8 bytes on a character boundary."""
  return text.encode('utf8')[:size].decode('utf8', errors='ignore')

def valid_name(name: str) -> bool:
  return bool(name) and all(ch.isprintable() and not ch.isspace() for ch in name)

def family_name(name: str, k: int) -> str:
  """The k-th name of `name`'s family: `name-k`, the base cut so the whole fits NAME_SIZE."""
  suffix = f'-{k}'
  return cut(name, NAME_SIZE - len(suffix)) + suffix

def _hash(password: str, salt: bytes) -> bytes:
  return hashlib.scrypt(password.encode('utf8'), salt=salt, **SCRYPT)

def _matches(row, password: str) -> bool:
  return secrets.compare_digest(_hash(password, row[3]), row[2])

def _row(conn: sqlite3.Connection, name: str):
  return conn.execute('SELECT id, name, hash, salt FROM accounts WHERE name = ?', (name,)).fetchone()

# accounts

def login(name: str, password: str) -> Account:
  """Logs in to, or creates, the account for a name and password.

  1. The account `name` with this password.
  2. Else the first of `name-1`, `name-2`, ... with this password.
  3. Else a new account: `name` if free, else the lowest free `name-k`.
  Names are case-insensitive and at most NAME_SIZE bytes.
  """
  name = cut(name.strip(), NAME_SIZE)
  if not valid_name(name):
    raise LoginError(LOGIN_ERROR.USERNAME_MALFORMED)
  if not password:
    raise LoginError(LOGIN_ERROR.PASSWORD_MALFORMED)
  try:
    with _transaction() as conn:
      candidates = [name]
      k = 1
      while True:
        row = _row(conn, candidates[-1])
        if row is None:
          break
        if _matches(row, password):
          return Account(row[0], row[1])
        candidates.append(family_name(name, k))
        k += 1
      # the last candidate is the first free name of the family
      new_name = candidates[-1]
      salt = secrets.token_bytes(16)
      cur = conn.execute('INSERT INTO accounts (name, hash, salt, created) VALUES (?, ?, ?, ?)',
        (new_name, _hash(password, salt), salt, int(time.time())))
      return Account(cur.lastrowid, new_name)
  except sqlite3.Error as err:
    print(f'Accounts database error: {err!r}')
    raise LoginError(LOGIN_ERROR.DATABASE_FAILED)

def find(name: str) -> Account | None:
  """The account of that name (any case), or None."""
  rows = _query('SELECT id, name FROM accounts WHERE name = ?', (name,))
  return Account(*rows[0]) if rows else None

# sessions: the game keeps the name typed at login in later connections (proxies, lobby)

def bind_session(typed: str, ip: str, account: Account):
  """Records that the game at `ip` that logged in as `typed` uses `account`."""
  with _transaction() as conn:
    conn.execute('INSERT OR REPLACE INTO sessions (typed, ip, account, updated) VALUES (?, ?, ?, ?)',
      (typed, ip, account.id, int(time.time())))

def session(typed: str, ip: str) -> Account | None:
  """The account of the game at `ip` that logged in as `typed`, or None."""
  rows = _query('''SELECT accounts.id, accounts.name FROM sessions JOIN accounts ON accounts.id = sessions.account
    WHERE sessions.typed = ? AND sessions.ip = ?''', (typed, ip))
  return Account(*rows[0]) if rows else None

def account_name(typed: str, ip: str) -> str:
  """The account name of the game at `ip` that logged in as `typed`; `typed` if there's no session."""
  account = session(typed, ip)
  return account.name if account else typed

# players per lobby: the lobby service counts them, the router's lobby list (another process) shows them

def set_lobby_counts(counts: dict[int, int]):
  """Replaces the number of players in each lobby."""
  with _transaction() as conn:
    conn.execute('DELETE FROM lobby_members')
    conn.executemany('INSERT INTO lobby_members (lobby, members) VALUES (?, ?)', counts.items())

def lobby_counts() -> dict[int, int]:
  """The number of players in each lobby that has any."""
  return dict(_query('SELECT lobby, members FROM lobby_members'))

# rooms: the lobby service knows who is in them, the IRC service (another process) lets a room's host announce them

ROOM_CHANNEL = re.compile(r'#LobbyGrp[0-9]+\.([0-9]+)')
"""The IRC channel of a group: `#LobbyGrp<lobby server id>.<group id>`."""

MEMBER_NAME_SIZE = 36
"""The game's buffer for a member name, terminator included (0x004242d0, 0x00424f00)."""

ANNOUNCE_WINDOW = 60
"""Seconds after a player left its room during which the host may still announce it."""

def set_room_members(rows: list[tuple[int, str, str, bool, float | None]]):
  """Replaces the rooms' members: (room id, account name, typed name, is the host, when it left or None)."""
  with _transaction() as conn:
    conn.execute('DELETE FROM room_members')
    conn.executemany('INSERT INTO room_members (room, name, typed, host, gone) VALUES (?, ?, ?, ?, ?)',
      [(room, name, typed, int(host), gone) for room, name, typed, host, gone in rows])

def announces(nick: str, channel: str, text: str) -> bool:
  """
  Whether the game of `nick` may post the chat announcement `text` (without its type digit) in an IRC channel, besides
  the ones about itself. A room's host announces the players joining and leaving its room in the room's channel ("bob
  has joined the game", "bob has left the game"), in the game's language, starting with the player's name as the host
  was told it; the players don't announce each other. So the host of the room may announce the room's members, and
  the players who left it in the last minute; nobody else may announce anyone.
  """
  match = ROOM_CHANNEL.fullmatch(channel)
  if match is None:
    return False
  rows = _query('SELECT name, typed, host, gone FROM room_members WHERE room = ?', (int(match.group(1)),))
  nick = nick.lower()
  if not any(host and gone is None and nick in (name.lower(), typed.lower()) for name, typed, host, gone in rows):
    return False
  now = time.time()
  return any(text.startswith(cap(name, MEMBER_NAME_SIZE) + ' ') for name, typed, host, gone in rows
    if name and (gone is None or now - gone <= ANNOUNCE_WINDOW))

# whether a game is still logged in: the router and its wait module refresh its session while it talks to them,
# at least every 30 s while it is online; the IRC service, another process, asks

SESSION_TOUCH = 60
"""Seconds between refreshes of a connected game's session."""

SESSION_LIVE = 180
"""A session refreshed within this many seconds belongs to a game still logged in."""

def touch_session(typed: str, ip: str):
  """Records that the game at `ip` that logged in as `typed` is still connected."""
  with _transaction() as conn:
    conn.execute('UPDATE sessions SET updated = ? WHERE typed = ? AND ip = ?', (int(time.time()), typed, ip))

def keep_session(clt):
  """Refreshes the session of a logged-in `client.TcpClient`, at most every SESSION_TOUCH seconds."""
  now = time.monotonic()
  if clt.account is not None and clt.typed_name and now - clt.session_touched >= SESSION_TOUCH:
    clt.session_touched = now
    touch_session(clt.typed_name, clt.addr[0])

def logged_in(name: str, ip: str, within: float = SESSION_LIVE) -> bool:
  """Whether a game at `ip` logged in as `name` (the name typed or the account's) and was connected in the last `within` seconds."""
  rows = _query('''SELECT 1 FROM sessions JOIN accounts ON accounts.id = sessions.account
    WHERE sessions.ip = ? AND (sessions.typed = ? OR accounts.name = ?) AND sessions.updated >= ? LIMIT 1''',
    (ip, name, name, int(time.time() - within)))
  return bool(rows)

# friends

def add_friend(account: Account, friend: Account):
  with _transaction() as conn:
    conn.execute('INSERT OR IGNORE INTO friends (account, friend) VALUES (?, ?)', (account.id, friend.id))

def remove_friend(account: Account, friend: Account):
  with _transaction() as conn:
    conn.execute('DELETE FROM friends WHERE account = ? AND friend = ?', (account.id, friend.id))

def friends(account: Account) -> list[Account]:
  """The account's friends, by name."""
  rows = _query('''SELECT accounts.id, accounts.name FROM friends JOIN accounts ON accounts.id = friends.friend
    WHERE friends.account = ? ORDER BY accounts.name''', (account.id,))
  return [Account(*row) for row in rows]

def friend_of(account: Account) -> list[Account]:
  """The accounts that have this one as a friend."""
  rows = _query('''SELECT accounts.id, accounts.name FROM friends JOIN accounts ON accounts.id = friends.account
    WHERE friends.friend = ?''', (account.id,))
  return [Account(*row) for row in rows]
# ladder stats

def ladder_stats(account: Account, ladder: str) -> dict[str, int]:
  """The account's stored ladder fields."""
  rows = _query('SELECT field, value FROM ladder WHERE account = ? AND game = ?', (account.id, ladder))
  return dict(rows)

def update_ladder(ladder: str, ids: list[int], update):
  """Reads the stored fields of the accounts and writes what `update({id: {field: value}})` returns
  ({id: {field: value}}), in one transaction, so results of concurrent matches can't interleave."""
  with _transaction() as conn:
    stored = {aid: dict(conn.execute('SELECT field, value FROM ladder WHERE account = ? AND game = ?', (aid, ladder)).fetchall())
      for aid in ids}
    for aid, stats in update(stored).items():
      conn.executemany('INSERT OR REPLACE INTO ladder (account, game, field, value) VALUES (?, ?, ?, ?)',
        [(aid, ladder, field, int(value)) for field, value in stats.items()])

def set_ladder_stats(account: Account, ladder: str, stats: dict[str, int]):
  with _transaction() as conn:
    conn.executemany('INSERT OR REPLACE INTO ladder (account, game, field, value) VALUES (?, ?, ?, ?)',
      [(account.id, ladder, field, int(value)) for field, value in stats.items()])
# profile data

def profile(account: Account, game: str) -> bytes | None:
  rows = _query('SELECT data FROM profiles WHERE account = ? AND game = ?', (account.id, game))
  return bytes(rows[0][0]) if rows else None

def set_profile(account: Account, game: str, data: bytes):
  with _transaction() as conn:
    conn.execute('INSERT OR REPLACE INTO profiles (account, game, data) VALUES (?, ?, ?)', (account.id, game, data))
