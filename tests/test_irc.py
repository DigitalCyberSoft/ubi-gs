import sys, os, socket, threading, time, unittest
# relative module import stuff
root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.append(root_dir)
sys.path.append(os.path.join(root_dir, 'homm5'))
import gsirc, ircm, utils

CHANNEL = '#LobbyGrp1.1000'

def frame(line: str):
  """A line as the game sends it: u16be length + Blowfish payload."""
  return bytes(ircm.IRCMessage.from_str(line + '\r\n'))

class Player:
  """A game client connected to the IRC service."""
  def __init__(self, port: int, nick: str = None):
    self.sock = socket.create_connection(('127.0.0.1', port), timeout=2)
    self.buf = b''
    self.lines = []
    if nick is not None:
      self.send(f'NICK :{nick}', f'USER HEROES_657d2c2ebadc6a1d +i 0 :{nick}')

  def send(self, *lines: str):
    self.sock.sendall(b''.join(frame(l) for l in lines))

  def read(self, wait: float = 0.4):
    """Collects the lines received within `wait` seconds."""
    end = time.monotonic() + wait
    while time.monotonic() < end:
      self.sock.settimeout(max(0.01, end - time.monotonic()))
      try:
        data = self.sock.recv(4096)
      except socket.timeout:
        break
      if not data:
        break
      self.buf += data
      while len(self.buf) >= 2 and len(self.buf) >= 2 + utils.read_u16_be(self.buf[:2]):
        size = utils.read_u16_be(self.buf[:2])
        msg = ircm.IRCMessage(self.buf[:2 + size])
        self.buf = self.buf[2 + size:]
        self.lines += [l for l in msg.payload.rstrip(b'\x00').decode(errors='replace').split('\r\n') if l]
    lines, self.lines = self.lines, []
    return lines

  def alive(self):
    """The server still answers this connection."""
    self.send('PING :check')
    return any('PONG' in l for l in self.read())

  def close(self):
    self.sock.close()

class IrcTestCase(unittest.TestCase):
  def setUp(self):
    self.server = gsirc.IRCServer(('127.0.0.1', 0), gsirc.IRCClient)
    self.port = self.server.server_address[1]
    threading.Thread(target=self.server.serve_forever, daemon=True).start()
    self.players = []

  def tearDown(self):
    for p in self.players:
      p.close()
    self.server.shutdown()
    self.server.server_close()

  def player(self, nick: str = None, join: bool = True):
    p = Player(self.port, nick)
    self.players.append(p)
    if nick is not None and join:
      p.send(f'JOIN :{CHANNEL}')
    p.read()
    return p

class ChatTests(IrcTestCase):
  """Chat lines can't pretend to come from someone else."""
  def test_own_line_relayed(self):
    alice, bob = self.player('alice'), self.player('bob')
    alice.read()
    bob.send(f'PRIVMSG {CHANNEL} :bob%16777215%9%0%0%Arial%1hello')
    self.assertTrue(any(l.endswith('PRIVMSG #LobbyGrp1.1000 :bob%16777215%9%0%0%Arial%1hello') for l in alice.read()))

  def test_name_is_the_sender(self):
    alice, bob = self.player('alice'), self.player('bob')
    alice.read()
    bob.send(f'PRIVMSG {CHANNEL} :alice%16777215%9%0%0%Arial%1I give up')
    lines = [l for l in alice.read() if 'PRIVMSG' in l]
    self.assertEqual(len(lines), 1)
    self.assertIn(':bob%16777215%9%0%0%Arial%1I give up', lines[0])

  def test_announcements_about_others_dropped(self):
    alice, bob = self.player('alice'), self.player('bob')
    alice.read()
    bob.send(f'PRIVMSG {CHANNEL} :bob%16777215%9%0%0%Arial%2alice has left the game')
    self.assertFalse(any('PRIVMSG' in l for l in alice.read()))
    bob.send(f'PRIVMSG {CHANNEL} :bob%16777215%9%0%0%Arial%2bob has joined the game')
    self.assertTrue(any('2bob has joined the game' in l for l in alice.read()))

  def test_malformed_payloads_dropped(self):
    alice, bob = self.player('alice'), self.player('bob')
    alice.read()
    for payload in ['plain text', 'bob%red%9%0%0%Arial%1hi', 'bob%1%9%0%0%<b>%1hi']:
      bob.send(f'PRIVMSG {CHANNEL} :{payload}')
    self.assertFalse(any('PRIVMSG' in l for l in alice.read()))
    self.assertTrue(bob.alive())

  def test_code_page_text(self):
    # chat in the game's code page comes back byte for byte (#55)
    alice, bob = self.player('alice'), self.player('bob')
    alice.read()
    msg = ircm.IRCMessage.from_str('')
    text = 'Привет, café'.encode('cp1251', errors='ignore') + b' caf\xe9'
    msg.payload = b'PRIVMSG ' + CHANNEL.encode() + b' :bob%16777215%9%0%0%Arial%1' + text + b'\r\n'
    bob.sock.sendall(bytes(msg))
    alice.sock.settimeout(1)
    data = b''
    try:
      while text not in data:
        chunk = alice.sock.recv(4096)
        if not chunk:
          break
        alice.buf += chunk
        while len(alice.buf) >= 2 and len(alice.buf) >= 2 + utils.read_u16_be(alice.buf[:2]):
          size = utils.read_u16_be(alice.buf[:2])
          data += ircm.IRCMessage(alice.buf[:2 + size]).payload
          alice.buf = alice.buf[2 + size:]
    except socket.timeout:
      pass
    self.assertIn(b'%1' + text + b'\r\n', data)
    self.assertNotIn(b'\xef\xbf\xbd', data)

class RobustnessTests(IrcTestCase):
  """Bad input gets an error reply, not a dropped connection."""
  def test_empty_parameters(self):
    p = self.player()
    for line in ['NICK :', 'NICK', 'JOIN ,', 'JOIN', 'MODE x', 'MODE', 'PRIVMSG', 'NOTICE #x']:
      p.send(line)
      self.assertTrue(p.alive(), line)
    p.send('NICK :alice')
    self.assertTrue(any(' 001 alice ' in l for l in p.read()))

  def test_invalid_utf8(self):
    p = self.player('alice')
    p.sock.sendall(bytes(ircm.IRCMessage.from_str('PRIVMSG #x :\xff\r\n')))
    msg = ircm.IRCMessage.from_str('')
    msg.payload = b'PRIVMSG ' + CHANNEL.encode() + b' :alice%1%9%0%0%Arial%1\xff\xfe\r\n'
    p.sock.sendall(bytes(msg))
    self.assertTrue(p.alive())

class ProtocolTests(IrcTestCase):
  """The parts of IRC the game uses."""
  def test_part_as_the_game_sends_it(self):
    alice, bob = self.player('alice'), self.player('bob')
    alice.read()
    alice.send(f'PART {CHANNEL} :')
    self.assertTrue(any(l.endswith(f'PART {CHANNEL} :') for l in bob.read()))
    self.assertFalse(any(' 403 ' in l for l in alice.read()))
    # alice is no longer in the channel
    bob.send(f'PRIVMSG {CHANNEL} :bob%16777215%9%0%0%Arial%1anyone?')
    self.assertFalse(any('PRIVMSG' in l for l in alice.read()))
    alice.send(f'PART {CHANNEL} :')
    self.assertTrue(any(' 442 ' in l for l in alice.read()))

  def test_split_frame(self):
    p = self.player()
    data = frame('NICK :alice') + frame('USER HEROES_657d2c2ebadc6a1d +i 0 :alice')
    p.sock.sendall(data[:3])
    time.sleep(0.2)
    p.sock.sendall(data[3:20])
    time.sleep(0.2)
    p.sock.sendall(data[20:])
    self.assertTrue(any(' 001 alice ' in l for l in p.read()))

  def test_dot_in_nick(self):
    p = self.player('a.b', join=False)
    p.send('PING :x')
    lines = p.read()
    self.assertFalse(any(' 432 ' in l for l in lines))
    self.assertTrue(p.alive())

  def test_reconnect_takes_over_nick(self):
    old = self.player('alice')
    new = self.player('alice')
    new.send('PING :x')
    lines = new.read()
    self.assertFalse(any(' 433 ' in l for l in lines))
    # the stale connection is closed
    old.sock.settimeout(2)
    try:
      while old.sock.recv(4096):
        pass
      closed = True
    except socket.timeout:
      closed = False
    except OSError:
      closed = True
    self.assertTrue(closed)
    # and the new one gets the channel traffic
    bob = self.player('bob')
    new.read()
    bob.send(f'PRIVMSG {CHANNEL} :bob%16777215%9%0%0%Arial%1hi')
    new.send(f'JOIN :{CHANNEL}')
    new.read()
    bob.send(f'PRIVMSG {CHANNEL} :bob%16777215%9%0%0%Arial%1hi again')
    self.assertTrue(any('1hi again' in l for l in new.read()))

if __name__ == '__main__':
  unittest.main()
