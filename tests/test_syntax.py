import sys, os, compileall, io, contextlib, types, unittest, warnings
# relative module import stuff
root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.append(root_dir)
sys.path.append(os.path.join(root_dir, 'homm5'))
import gsirc

class SyntaxTests(unittest.TestCase):
  def test_no_invalid_escapes(self):
    """Every module compiles with warnings as errors (invalid escapes become errors in future Pythons)."""
    with warnings.catch_warnings():
      warnings.simplefilter('error')
      out = io.StringIO()
      with contextlib.redirect_stdout(out):
        ok = compileall.compile_dir(root_dir, quiet=1, force=True, legacy=False, workers=1)
    self.assertTrue(ok, out.getvalue())

class ChannelNameTests(unittest.TestCase):
  """`JOIN` accepts the game's channel names: `#` and letters, digits, `_` or `.`."""
  def client(self):
    clt = object.__new__(gsirc.IRCClient)
    clt.nick, clt.user, clt.channels, clt.send_queue = 'alice', 'HEROES', {}, []
    clt.server = types.SimpleNamespace(servername='localhost', channels={}, clients={})
    return clt

  def test_valid(self):
    for name in ['#LobbyGrp1.1000', '#a', '#x_y.z9']:
      clt = self.client()
      clt.handle_join(f':{name}')
      self.assertIn(name, clt.channels)

  def test_invalid(self):
    for name in ['#bad-name', 'LobbyGrp1', '#a\\b', '#']:
      clt = self.client()
      with self.assertRaises(gsirc.IRCError, msg=name):
        clt.handle_join(name)

if __name__ == '__main__':
  unittest.main()
