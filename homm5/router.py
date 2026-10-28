import socket, sys, os, threading
# relative module import stuff
root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.append(root_dir)
import limits
import ports
import gsm, pkc, client, h5

SERVER_ADDRESS = h5.ENDPOINTS["router"]
"""Address of the router service."""

WAIT_MODULE = h5.ENDPOINTS["router_wm"]
"""Address of the wait module service the game will be redirected to."""

CLIENTS: list[client.TcpClient] = []
"""Global list of connected game clients."""

def handle_req(clt: client.TcpClient, req: gsm.Message):
  """Handler for `gsm.Message` requests."""
  res = None
  match req.header.type:
    case gsm.MESSAGE_TYPE.STILLALIVE:
      res = gsm.StillAliveResponse(req)
    case gsm.MESSAGE_TYPE.JOINWAITMODULE:
      res = gsm.JoinWaitModuleResponse(req, WAIT_MODULE)
    case gsm.MESSAGE_TYPE.LOGIN:
      # todo: actual user auth here
      clt.username = req.dl.lst[0]
      res = gsm.LoginResponse(req)
    case gsm.MESSAGE_TYPE.KEY_EXCHANGE:
      match req.dl.lst[0]:
        case '1':
          clt.game_pubkey = pkc.RsaPublicKey.from_buf(req.dl.lst[1][2]).to_pubkey()
          # keygen
          pub_key, priv_key = pkc.keygen()
          clt.sv_pubkey = pub_key
          clt.sv_privkey = priv_key
          res = gsm.KeyExchangeResponse(req, clt)
        case '2':
          enc_bf_key = bytes(req.dl.lst[1][2])
          bf_key = pkc.decrypt(enc_bf_key, clt.sv_privkey)
          clt.game_bf_key = bf_key
          res = gsm.KeyExchangeResponse(req, clt)
        case _:
          raise BufferError(f'Unknown reqId ({req.dl[0]}) for a {req.header.type.name} message.')
    case _:
      raise NotImplementedError(f"No request handler for {req.header.type.name} messages.")
  return res

def handle_client(clt: client.TcpClient):
  """Serves a connected game client until it disconnects."""
  try:
    while True:
      data = clt.conn.recv(4096)
      if data:
        req = gsm.Message(clt.sv_bf_key, in_buf=data)
        print(req)
        res = handle_req(clt, req)
        if res:
          print(res)
          clt.conn.sendall(bytes(res))
        elif req.header.type != gsm.MESSAGE_TYPE.STILLALIVE:
          clt.conn.sendall(data)
      else:
        print("No more data from", clt.addr)
        break
  finally:
    clt.conn.close()
    CLIENTS.remove(clt)

def start_server():
  sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
  ports.bind(sock, SERVER_ADDRESS, "router")
  print(f"Router service is listening on port {SERVER_ADDRESS[1]}")
  sock.listen(5)
    
  while True:
    clt = client.TcpClient(sock.accept())
    if not limits.TCP.admit(clt):
      continue
    CLIENTS.append(clt)
    print(f"Connection from {clt.addr}")
    threading.Thread(target=limits.TCP.serve, args=(handle_client, clt), daemon=True).start()

if __name__ == "__main__":
    start_server()
