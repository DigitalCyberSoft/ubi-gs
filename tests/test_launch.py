import sys, os, re, unittest
# relative module import stuff
root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.append(os.path.join(root_dir, 'homm5'))
import launch

class LaunchTests(unittest.TestCase):
  """`launch.py` starts the same services as `homm5.bat`."""
  def test_services_exist(self):
    for name in launch.SERVICES:
      self.assertTrue(os.path.isfile(os.path.join(launch.HERE, f'{name}.py')), name)

  def test_same_as_bat(self):
    with open(os.path.join(launch.HERE, 'homm5.bat')) as bat:
      started = re.findall(r'python (\w+)\.py', bat.read())
    self.assertEqual(launch.SERVICES, started)

if __name__ == '__main__':
  unittest.main()
