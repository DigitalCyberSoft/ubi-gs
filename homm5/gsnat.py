import secrets, socket, sys, os
# relative module import stuff
root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.append(root_dir)
import srp, client, utils, gsm, h5

SERVER_ADDRESS = h5.ENDPOINTS["nat"]
"""Address of the NAT service."""

CLIENTS: list[client.NatClient] = []
"""Global list of connected game clients."""

def new_ping_id(clients: list[client.NatClient]):
  """Returns a random nonzero 32-bit NAT ping id not used by another client."""
  while True:
    ping_id = secrets.randbits(32)
    if ping_id != 0 and client.NatClient.find_by_ping(ping_id, clients) is None:
      return ping_id

sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.bind(SERVER_ADDRESS)

print(f"GSNAT server is listening on port {SERVER_ADDRESS[1]}")

while True:
  data, address = sock.recvfrom(1024)
  packet_size = len(data)
  # NAT pings, sent from the game's own port with the id from NAT_MSG.PORT_ID
  if packet_size < srp.SRP_HEADER_SIZE:
    print(f"<REQ: PING> {address[0]}:{address[1]}")
    if packet_size != 4:
      continue
    clt = client.NatClient.find_by_ping(utils.read_u32_be(data), CLIENTS)
    if clt is None or clt.nat_req is None:
      print(f'unknown ping id {data.hex()}')
      continue
    # the address the ping came from, over the SRP connection; this also keeps it alive (120s timeout)
    msg = gsm.NatResponse(clt.nat_req, gsm.NAT_MSG.ADDRESS, utils.ipv4_to_u32_be(address[0]), address[1])
    res = srp.SRPResponse(None, clt, msg)
    sock.sendto(bytes(res), (clt.addr, clt.port))
    print(res)
  else:
    clt = client.NatClient.find(address, CLIENTS)
    req = srp.SRPRequest(data)
    print(req)
    if clt is None and srp.SRPHeaderFlags.FIN.name not in req.segment.header.flags:
      clt = client.NatClient(address, None, None)
      CLIENTS.append(clt)
      print(f'added client {clt.addr}:{clt.port}')
    # ConnectHost (SYN)
    if req.segment.window:
      res = srp.SRPResponse(req, clt)
      sock.sendto(bytes(res), address)
      print(res)
    # NAT message (address request), the address is sent when the game pings
    elif req.segment.msg:
      clt.nat_req = req.segment.msg
      if clt.ping_id is None:
        clt.ping_id = new_ping_id(CLIENTS)
      msg = gsm.NatResponse(clt.nat_req, gsm.NAT_MSG.PORT_ID, clt.ping_id, SERVER_ADDRESS[1])
      res = srp.SRPResponse(req, clt, msg)
      sock.sendto(bytes(res), address)
      print(res)
    # Disconnect (FIN)
    if srp.SRPHeaderFlags.FIN.name in req.segment.header.flags:
      if clt is not None and client.NatClient.find((clt.addr, clt.port), CLIENTS):
        CLIENTS.remove(clt)
        print(f'removed client {clt.addr}:{clt.port}')
