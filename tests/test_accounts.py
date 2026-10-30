import sys, os, tempfile, threading, multiprocessing, sqlite3, unittest
# relative module import stuff
root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.append(root_dir)
sys.path.append(os.path.join(root_dir, 'homm5'))
import accounts, client, gsm, lobby
from data import List
import router, router_wm

def race_login(path: str, name: str, password: str, queue):
  """Another process logging in: the account name, or the refusal."""
  accounts.configure(path)
  try:
    queue.put(accounts.login(name, password).name)
  except accounts.LoginError as err:
    queue.put(err.reason.name)

class FakeConn:
  def __init__(self):
    self.sent = []
  def sendall(self, data):
    self.sent.append(data)
  def shutdown(self, how):
    self.closed = True

class AccountTestCase(unittest.TestCase):
  def setUp(self):
    self.dir = tempfile.TemporaryDirectory()
    self.path = os.path.join(self.dir.name, 'ubigs.db')
    accounts.configure(self.path)

  def tearDown(self):
    accounts.configure(None)
    self.dir.cleanup()

  def assertRefused(self, name: str, password: str):
    """The login is refused as a wrong password."""
    with self.assertRaises(accounts.LoginError) as ctx:
      accounts.login(name, password)
    self.assertEqual(ctx.exception.reason, accounts.LOGIN_ERROR.INVALID_PASSWORD)

  def count(self) -> int:
    return accounts.db().execute('SELECT COUNT(*) FROM accounts').fetchone()[0]

class AccountTests(AccountTestCase):
  def test_first_login_creates(self):
    account = accounts.login('alice', 'secret')
    self.assertEqual(account.name, 'alice')
    self.assertEqual(accounts.login('alice', 'secret'), account)

  def test_only_hashes_stored(self):
    accounts.login('alice', 'secret')
    with open(self.path, 'rb') as file:
      data = file.read()
    for name in os.listdir(self.dir.name):
      with open(os.path.join(self.dir.name, name), 'rb') as file:
        data += file.read()
    self.assertNotIn(b'secret', data)

  def test_taken_name_wrong_password_refused(self):
    alice = accounts.login('alice', 'one')
    # every wrong password is refused, and creates nothing
    self.assertRefused('alice', 'two')
    self.assertRefused('alice', 'two')
    self.assertRefused('ALICE', 'three')
    self.assertEqual(accounts.login('alice', 'one'), alice)
    self.assertEqual(self.count(), 1)
    for k in range(1, 4):
      self.assertIsNone(accounts.find(accounts.family_name('alice', k)))

  def test_existing_family_accounts_kept(self):
    # name-k accounts from before wrong passwords were refused (here created under their own names) keep logging in
    # with the typed name and their password
    alice = accounts.login('alice', 'one')
    first = accounts.login('alice-1', 'two')
    second = accounts.login('alice-2', 'three')
    self.assertEqual(accounts.login('alice', 'two'), first)
    self.assertEqual(accounts.login('Alice', 'three'), second)
    self.assertEqual(accounts.login('alice', 'one'), alice)
    # a password of none of them is refused, and alice-3 isn't created
    self.assertRefused('alice', 'four')
    self.assertIsNone(accounts.find('alice-3'))
    self.assertEqual(self.count(), 3)

  def test_case_insensitive(self):
    account = accounts.login('Alice', 'one')
    self.assertEqual(accounts.login('ALICE', 'one'), account)
    self.assertRefused('alice', 'two')
    self.assertEqual(accounts.find('aLiCe'), account)

  def test_length(self):
    self.assertEqual(accounts.login('abcdefghijklmno', 'one').name, 'abcdefghijk')
    self.assertEqual(accounts.login('abcdefghijk', 'one').name, 'abcdefghijk')
    # a longer name is cut to the taken one
    self.assertRefused('abcdefghijklmnop', 'two')
    # family names are cut so the suffix fits
    self.assertEqual(accounts.family_name('abcdefghijk', 1), 'abcdefghi-1')

  def test_length_multibyte(self):
    # 'é' is 2 bytes in UTF-8, never split
    name = accounts.login('éééééééé', 'one').name
    self.assertEqual(name, 'ééééé')
    self.assertRefused('ééééé', 'two')
    self.assertEqual(accounts.family_name('ééééé', 1), 'éééé-1')

  def test_malformed(self):
    for name in ['', '   ', 'a b', 'a\tb']:
      with self.assertRaises(accounts.LoginError) as ctx:
        accounts.login(name, 'one')
      self.assertEqual(ctx.exception.reason, accounts.LOGIN_ERROR.USERNAME_MALFORMED)
    with self.assertRaises(accounts.LoginError) as ctx:
      accounts.login('alice', '')
    self.assertEqual(ctx.exception.reason, accounts.LOGIN_ERROR.PASSWORD_MALFORMED)

  def test_persistence(self):
    alice = accounts.login('alice', 'one')
    self.assertRefused('alice', 'two')
    # a restart
    accounts.configure(self.path)
    self.assertEqual(accounts.login('alice', 'one'), alice)
    self.assertRefused('alice', 'two')

  def test_corrupt_file(self):
    accounts.configure(None)
    with open(self.path, 'wb') as file:
      file.write(b'this is not a database' * 100)
    accounts.configure(self.path)
    self.assertEqual(accounts.login('alice', 'one').name, 'alice')
    aside = [name for name in os.listdir(self.dir.name) if '.corrupt-' in name]
    self.assertTrue(aside)

  def test_schema_version(self):
    accounts.login('alice', 'one')
    self.assertEqual(accounts.db().execute('PRAGMA user_version').fetchone()[0], accounts.SCHEMA_VERSION)

  def test_concurrent_first_logins(self):
    names = []
    def run(password):
      try:
        names.append(accounts.login('bob', password).name)
      except accounts.LoginError as err:
        names.append(err.reason.name)
    threads = [threading.Thread(target=run, args=(f'pw{i}',)) for i in range(4)]
    for thread in threads: thread.start()
    for thread in threads: thread.join()
    # the first creates bob, the others are wrong passwords
    self.assertEqual(sorted(names), ['INVALID_PASSWORD'] * 3 + ['bob'])
    self.assertEqual(self.count(), 1)

  def test_concurrent_same_password(self):
    names = []
    threads = [threading.Thread(target=lambda: names.append(accounts.login('bob', 'pw').name)) for _ in range(4)]
    for thread in threads: thread.start()
    for thread in threads: thread.join()
    self.assertEqual(names, ['bob'] * 4)

  def test_racing_processes(self):
    # two services or two servers on the same file: the transaction keeps the names apart
    accounts.configure(None)
    ctx = multiprocessing.get_context('spawn')
    queue = ctx.Queue()
    procs = [ctx.Process(target=race_login, args=(self.path, 'carol', f'pw{i}', queue)) for i in range(3)]
    for proc in procs: proc.start()
    for proc in procs: proc.join(60)
    self.assertEqual(sorted(queue.get(timeout=5) for _ in procs), ['INVALID_PASSWORD', 'INVALID_PASSWORD', 'carol'])

  def test_sessions(self):
    accounts.login('alice', 'one')
    second = accounts.login('alice-1', 'two')
    accounts.bind_session('alice', '10.0.0.3', second)
    self.assertEqual(accounts.account_name('alice', '10.0.0.3'), 'alice-1')
    self.assertEqual(accounts.account_name('ALICE', '10.0.0.3'), 'alice-1')
    # no session: the typed name
    self.assertEqual(accounts.account_name('alice', '10.0.0.9'), 'alice')

class WrongPasswordTests(AccountTestCase):
  """A login to a taken name is refused unless its password matches; no `name-k` is ever created.

  HommaRe's registration (`~r:` and a token) and challenge answers (`~1:` and an HMAC) are plain passwords to this
  server, so they follow the same rule: a `~r:` with the stored token logs in, any other is refused.
  """
  def test_rule(self):
    # a new name registers on its first login, and the right password logs in
    plain = accounts.login('testhifip', 'right')
    self.assertEqual(plain.name, 'testhifip')
    self.assertEqual(accounts.login('testhifip', 'right'), plain)
    # a wrong plain password is refused
    self.assertRefused('testhifip', 'wrong')
    self.assertRefused('TESTHIFIP', 'wrong2')
    # a ~r: registration for a new name registers, the same token logs in, another token is refused
    token = accounts.login('homma', '~r:abcdefghijklmnop')
    self.assertEqual(token.name, 'homma')
    self.assertEqual(accounts.login('homma', '~r:abcdefghijklmnop'), token)
    self.assertRefused('homma', '~r:ponmlkjihgfedcba')
    self.assertRefused('testhifip', '~r:right')
    # a ~1: answer that isn't the account's password is refused
    self.assertRefused('homma', '~1:AAAAAAAAAAAAAAAA')
    # nothing else was created
    self.assertEqual(self.count(), 2)
    for name in ['testhifip', 'homma']:
      self.assertIsNone(accounts.find(accounts.family_name(name, 1)))

class GameBytesPasswordTests(AccountTestCase):
  def test_hash_unchanged(self):
    """Passwords are still hashed from the UTF-8 of their Latin-1 reading, so existing databases keep working (#55)."""
    import hashlib
    password = b'p\xe4ss'.decode('latin-1')
    account = accounts.login('alice', password)
    row = accounts.db().execute('SELECT hash, salt FROM accounts WHERE id = ?', (account.id,)).fetchone()
    self.assertEqual(row[0], hashlib.scrypt('päss'.encode('utf8'), salt=row[1], **accounts.SCRYPT))
    self.assertEqual(accounts.login('alice', password), account)

class MemoryTests(unittest.TestCase):
  def tearDown(self):
    accounts.configure(None)

  def test_memory_only(self):
    accounts.configure(accounts.MEMORY)
    self.assertEqual(accounts.login('alice', 'one').name, 'alice')
    with self.assertRaises(accounts.LoginError):
      accounts.login('alice', 'two')
    # shared by the services of the process
    self.assertEqual(accounts.find('ALICE').name, 'alice')
    self.assertIsNone(accounts.find('alice-1'))

  def test_memory_env(self):
    previous = os.environ.get('UBIGS_DB')
    os.environ['UBIGS_DB'] = ':memory:'
    try:
      accounts.configure(None)
      self.assertEqual(accounts.db_path(), accounts.MEMORY)
      accounts.login('dave', 'one')
      accounts.configure(None)
      # gone after a restart
      self.assertIsNone(accounts.find('dave'))
    finally:
      if previous is None:
        del os.environ['UBIGS_DB']
      else:
        os.environ['UBIGS_DB'] = previous

def message(msg_type: gsm.MESSAGE_TYPE, data: list):
  header = gsm.GSMessageHeader.from_params(gsm.PROPERTY.GS, 1, msg_type, gsm.SENDER_RECEIVER.P, gsm.SENDER_RECEIVER.R)
  buf = bytes(gsm.GSMNotification(gsm.Message(None, header=header, dl=List(data))))
  return gsm.Message(None, in_buf=buf)

def router_client(ip: str):
  clt = client.TcpClient((FakeConn(), (ip, 50000)))
  router.CLIENTS.append(clt)
  return clt

class RouterLoginTests(AccountTestCase):
  def tearDown(self):
    router.CLIENTS.clear()
    super().tearDown()

  def login(self, ip: str, name: str, password: str):
    clt = router_client(ip)
    return clt, router.handle_req(clt, message(gsm.MESSAGE_TYPE.LOGIN, [name, password, 'HEROES_657d2c2ebadc6a1d']))

  def test_login_and_name(self):
    clt, res = self.login('10.0.0.2', 'alice', 'one')
    self.assertEqual(res.header.type, gsm.MESSAGE_TYPE.GSSUCCESS)
    # an alice-1 from before wrong passwords were refused, logging in as alice
    accounts.login('alice-1', 'two')
    clt2, res = self.login('10.0.0.3', 'alice', 'two')
    self.assertEqual(res.header.type, gsm.MESSAGE_TYPE.GSSUCCESS)
    self.assertEqual(clt2.username, 'alice-1')
    # the wait module and its PLAYERINFO (the game's IRC nick and display name) use the account name
    wm = client.TcpClient((FakeConn(), ('10.0.0.3', 50001)))
    router_wm.handle_req(wm, message(gsm.MESSAGE_TYPE.LOGINWAITMODULE, ['alice']))
    self.assertEqual(wm.username, 'alice-1')
    res = router_wm.handle_req(wm, message(gsm.MESSAGE_TYPE.PLAYERINFO, ['alice']))
    self.assertEqual(res.dl.lst[1][:2], ['alice-1', 'alice-1'])

  def test_refused(self):
    _, res = self.login('10.0.0.2', 'a b', 'one')
    self.assertEqual(res.header.type, gsm.MESSAGE_TYPE.GSFAIL)
    self.assertEqual(res.dl.lst, [b'f', [(2).to_bytes(4, 'little')]])

  def test_wrong_password(self):
    """A taken name with another password: INVALID_PASSWORD (15), the game's "The password is not correct"."""
    self.login('10.0.0.2', 'alice', 'one')
    clt, res = self.login('10.0.0.3', 'alice', 'two')
    self.assertEqual(res.header.type, gsm.MESSAGE_TYPE.GSFAIL)
    self.assertEqual(res.dl.lst, [b'f', [(15).to_bytes(4, 'little')]])
    self.assertIsNone(clt.account)
    self.assertIsNone(accounts.session('alice', '10.0.0.3'))
    self.assertIsNone(accounts.find('alice-1'))

  def test_lobby_names(self):
    self.login('10.0.0.2', 'alice', 'one')
    accounts.login('alice-1', 'two')
    self.login('10.0.0.3', 'alice', 'two')
    lobby.g_clients.clear()
    first = client.TcpClient((FakeConn(), ('10.0.0.2', 50002)))
    second = client.TcpClient((FakeConn(), ('10.0.0.3', 50002)))
    for clt in [first, second]:
      lobby.g_clients.append(clt)
      lobby.handle_req(clt, message(gsm.MESSAGE_TYPE.LOBBYSERVERLOGIN, ['alice', '1', clt.addr[0], '255.255.255.0', '0']))
      lobby.handle_req(clt, message(gsm.MESSAGE_TYPE.LOBBY_MSG, ['23', ['1', '', '384']]))
    self.assertEqual([first.username, second.username], ['alice', 'alice-1'])
    first.conn.sent.clear()
    lobby.send_group_info(first, 1, 384)
    members = gsm.Message(None, in_buf=first.conn.sent[-1]).dl.lst[1][4]
    self.assertEqual([m[0] for m in members], ['alice', 'alice-1'])
    # a player knows itself by the typed name
    lobby.send_group_info(second, 1, 384)
    members = gsm.Message(None, in_buf=second.conn.sent[-1]).dl.lst[1][4]
    self.assertEqual([m[0] for m in members], ['alice', 'alice'])
    lobby.g_clients.clear()

  def test_second_login_closes_older(self):
    old, _ = self.login('10.0.0.2', 'alice', 'one')
    new, _ = self.login('10.0.0.5', 'alice', 'one')
    self.assertTrue(getattr(old.conn, 'closed', False))
    self.assertFalse(getattr(new.conn, 'closed', False))

def age_sessions(seconds: int):
  with accounts._transaction() as conn:
    conn.execute('UPDATE sessions SET updated = updated - ?', (seconds,))

class LoggedInTests(AccountTestCase):
  """The IRC service asks whether a nick is a game still logged in from its address."""
  def test_fresh_session(self):
    accounts.bind_session('alice', '10.0.0.5', accounts.login('alice', 'one'))
    self.assertTrue(accounts.logged_in('alice', '10.0.0.5'))
    self.assertTrue(accounts.logged_in('ALICE', '10.0.0.5'))
    self.assertFalse(accounts.logged_in('alice', '10.0.0.6'))
    self.assertFalse(accounts.logged_in('bob', '10.0.0.5'))

  def test_account_name(self):
    # an earlier alice-1, logged in to as alice: the game may use the account name too
    accounts.login('alice', 'one')
    accounts.login('alice-1', 'two')
    account = accounts.login('alice', 'two')
    accounts.bind_session('alice', '10.0.0.6', account)
    self.assertTrue(accounts.logged_in(account.name, '10.0.0.6'))

  def test_stale_session(self):
    accounts.bind_session('alice', '10.0.0.5', accounts.login('alice', 'one'))
    age_sessions(accounts.SESSION_LIVE + 1)
    self.assertFalse(accounts.logged_in('alice', '10.0.0.5'))
    accounts.touch_session('alice', '10.0.0.5')
    self.assertTrue(accounts.logged_in('alice', '10.0.0.5'))

  def test_keep_session(self):
    account = accounts.login('alice', 'one')
    accounts.bind_session('alice', '10.0.0.5', account)
    clt = client.TcpClient((FakeConn(), ('10.0.0.5', 50000)))
    clt.account, clt.typed_name = account, 'alice'
    age_sessions(accounts.SESSION_LIVE + 1)
    # a message from the game refreshes its session...
    accounts.keep_session(clt)
    self.assertTrue(accounts.logged_in('alice', '10.0.0.5'))
    # ...at most every SESSION_TOUCH seconds
    age_sessions(accounts.SESSION_LIVE + 1)
    accounts.keep_session(clt)
    self.assertFalse(accounts.logged_in('alice', '10.0.0.5'))

if __name__ == '__main__':
  unittest.main()
