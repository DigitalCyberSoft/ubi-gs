import secrets, socket, sys, os, time
# relative module import stuff
root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.append(root_dir)
import ports
import srp, client, utils, gsm, h5

SERVER_ADDRESS = h5.ENDPOINTS["nat"]
"""Address of the NAT service."""

CLIENT_TIMEOUT = 150
"""Seconds of silence after which a client is dropped (the game gives up after 120)."""

TICK = 10
"""Seconds between checks for silent clients when no packets arrive."""

MAX_CLIENTS = int(os.environ.get('UBIGS_MAX_NAT_CLIENTS', '4096'))
"""Clients the NAT table holds; UDP source addresses can be spoofed, so the table needs a bound."""

MAX_CLIENTS_PER_IP = int(os.environ.get('UBIGS_MAX_PER_IP', '16'))
"""Clients the NAT table holds for one address (players behind one NAT share it)."""

CLIENTS: list[client.NatClient] = []
"""Global list of connected game clients."""

def new_ping_id(clients: list[client.NatClient]):
  """Returns a random nonzero 32-bit NAT ping id not used by another client."""
  while True:
    ping_id = secrets.randbits(32)
    if ping_id != 0 and client.NatClient.find_by_ping(ping_id, clients) is None:
      return ping_id

def handle_ping(data: bytes, address: tuple[str, int], now: float):
  """Handles a NAT ping, sent from the game's own port with the id from `NAT_MSG.PORT_ID`. Returns the packets to send."""
  print(f"<REQ: PING> {address[0]}:{address[1]}")
  if len(data) != 4:
    return []
  clt = client.NatClient.find_by_ping(utils.read_u32_be(data), CLIENTS)
  if clt is None or clt.nat_req is None:
    print(f'unknown ping id {data.hex()}')
    return []
  clt.last_seen = now
  if not clt.address_pending:
    # already answered: a bare ACK keeps the SRP connection alive (the game drops it after 120s)
    print(f'<RES: ACK> {clt.addr}:{clt.port}')
    return [(srp.ack_segment(clt), (clt.addr, clt.port))]
  clt.address_pending = False
  # the address the ping came from, over the SRP connection
  msg = gsm.NatResponse(clt.nat_req, gsm.NAT_MSG.ADDRESS, utils.ipv4_to_u32_be(address[0]), address[1])
  res = srp.SRPResponse(None, clt, msg)
  print(res)
  return [(bytes(res), (clt.addr, clt.port))]

def handle_segment(data: bytes, address: tuple[str, int], now: float):
  """Handles an SRP segment. Returns the packets to send."""
  packets = []
  clt = client.NatClient.find(address, CLIENTS)
  req = srp.SRPRequest(data)
  print(req)
  # a new connection from a known address (e.g. the game restarted) starts over
  if clt is not None and srp.SRPHeaderFlags.SYN.name in req.segment.header.flags:
    CLIENTS.remove(clt)
    clt = None
  if clt is None and srp.SRPHeaderFlags.FIN.name not in req.segment.header.flags:
    if not has_room(address[0]):
      print(f'NAT table full, ignoring {address[0]}:{address[1]}')
      return packets
    clt = client.NatClient(address, None, None)
    CLIENTS.append(clt)
    print(f'added client {clt.addr}:{clt.port}')
  if clt is not None:
    clt.last_seen = now
  # ConnectHost (SYN)
  if req.segment.window:
    res = srp.SRPResponse(req, clt)
    packets.append((bytes(res), address))
    print(res)
  # NAT message (address request), the address is sent when the game pings
  elif req.segment.msg:
    clt.nat_req = req.segment.msg
    clt.address_pending = True
    if clt.ping_id is None:
      clt.ping_id = new_ping_id(CLIENTS)
    msg = gsm.NatResponse(clt.nat_req, gsm.NAT_MSG.PORT_ID, clt.ping_id, SERVER_ADDRESS[1])
    res = srp.SRPResponse(req, clt, msg)
    packets.append((bytes(res), address))
    print(res)
  # Disconnect (FIN)
  if srp.SRPHeaderFlags.FIN.name in req.segment.header.flags:
    if clt is not None and client.NatClient.find((clt.addr, clt.port), CLIENTS):
      CLIENTS.remove(clt)
      print(f'removed client {clt.addr}:{clt.port}')
  return packets

def has_room(ip: str):
  """There is room in the table for another client from the address."""
  return len(CLIENTS) < MAX_CLIENTS and sum(1 for c in CLIENTS if c.addr == ip) < MAX_CLIENTS_PER_IP

def handle_packet(data: bytes, address: tuple[str, int], now: float):
  """Handles a NAT service datagram. Returns the packets to send."""
  if len(data) < srp.SRP_HEADER_SIZE:
    return handle_ping(data, address, now)
  return handle_segment(data, address, now)

def remove_silent(now: float):
  """Drops the clients nothing has arrived from for `CLIENT_TIMEOUT` seconds."""
  for clt in [c for c in CLIENTS if now - c.last_seen > CLIENT_TIMEOUT]:
    CLIENTS.remove(clt)
    print(f'expired client {clt.addr}:{clt.port}')

def start_server():
  sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
  ports.bind(sock, SERVER_ADDRESS, "nat")
  sock.settimeout(TICK)
  print(f"GSNAT server is listening on port {SERVER_ADDRESS[1]}")

  while True:
    try:
      data, address = sock.recvfrom(1024)
    except socket.timeout:
      data = None
    now = time.monotonic()
    remove_silent(now)
    if data is not None:
      # one bad packet must not stop the service
      try:
        packets = handle_packet(data, address, now)
      except Exception as err:
        print(f"Bad packet from {address[0]}:{address[1]}: {err!r}")
        continue
      for packet, addr in packets:
        sock.sendto(packet, addr)

if __name__ == "__main__":
  start_server()
