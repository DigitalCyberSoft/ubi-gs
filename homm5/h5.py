import os
from enum import Enum

HOST = os.environ.get('UBIGS_HOST') or None
"""Address given to the game in redirects (wait modules, proxies, lobby), env UBIGS_HOST.
Unset, the game is sent the address it reached the service on; set it behind NAT or port forwarding."""

BIND_HOST = os.environ.get('UBIGS_BIND', '0.0.0.0')
"""Address the services listen on (env UBIGS_BIND), all interfaces by default."""

ENDPOINTS = {
  "router": (BIND_HOST, 7777),
  "irc": (BIND_HOST, 7779),
  "cdkey": (BIND_HOST, 7780),
  "nat": (BIND_HOST, 7781),
  "router_wm": (BIND_HOST, 7782),
  "proxy": (BIND_HOST, 7783),
  "proxy_wm": (BIND_HOST, 7784),
  "lobby": (BIND_HOST, 7785)
}
"""Service endpoints for the Game Service network, as listened on."""

def advertised(endpoint: tuple[str, int], conn) -> tuple[str, int]:
  """Address of an endpoint for the game connected on `conn`: UBIGS_HOST, else the local address it reached."""
  return (HOST or conn.getsockname()[0], endpoint[1])

class PROXY_MODULE(Enum):
  """Proxy server IDs for proxy modules."""
  PERSISTENT_DATA = 1
  LADDER_QUERY = 2
  REMOTE_ALGORITHM = 3
  CLAN_SERVICE = 4

class GAME_MODE(Enum):
  """Heroes of Might and Magic 5 game mode."""
  STANDARD = 0
  RATING = 1
  DUEL = 2
  DUEL2 = 3
  """Same as `DUEL`."""
