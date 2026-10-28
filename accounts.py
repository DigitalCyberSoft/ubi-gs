"""Player accounts, friends, ladder stats and profile data in SQLite.

The database is a file next to this module (`ubigs.db`), or UBIGS_DB. `UBIGS_DB=:memory:` keeps everything in memory
only: it is shared by the services running in one process and lost when that process ends.
Passwords are stored as salted scrypt hashes only.
"""
import hashlib, os, secrets, sqlite3, threading, time
from dataclasses import dataclass
from enum import Enum

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

# profile data

def profile(account: Account, game: str) -> bytes | None:
  rows = _query('SELECT data FROM profiles WHERE account = ? AND game = ?', (account.id, game))
  return bytes(rows[0][0]) if rows else None

def set_profile(account: Account, game: str, data: bytes):
  with _transaction() as conn:
    conn.execute('INSERT OR REPLACE INTO profiles (account, game, data) VALUES (?, ?, ?)', (account.id, game, data))
