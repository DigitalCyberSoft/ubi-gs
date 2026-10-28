import socket, sys, os, threading
# relative module import stuff
root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.append(root_dir)
import gsm, pkc, client, h5

SERVER_ADDRESS = h5.ENDPOINTS["proxy_wm"]
"""Address of the proxy's wait module service."""

CLIENTS: list[client.TcpClient] = []
"""Global list of connected game clients."""

class ModuleUnavailable(Exception):
  """A proxy module request this server has no service for."""

def handle_req(clt: client.TcpClient, req: gsm.Message):
  """Handler for `gsm.Message` requests."""
  res = None
  match req.header.type:
    case gsm.MESSAGE_TYPE.STILLALIVE:
      res = gsm.StillAliveResponse(req)
    case gsm.MESSAGE_TYPE.LOGINWAITMODULE:
      clt.username = req.dl.lst[0]
      res = gsm.ProxyLoginWaitModuleResponse(req)
    case gsm.MESSAGE_TYPE.PROXY_HANDLER:
      # module requests (e.g. ladder queries, 1281): closing the module's connection fails them,
      # then the game carries on (it sends its LOBBY_MSG.SET_PLAYER_INFO without ladder data)
      raise ModuleUnavailable(req.dl.lst[0])
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
        if req.header.size < len(data):
          bundle = gsm.GSMessageBundle(req, data[req.header.size:], clt)
          print(bundle)
          for msg in bundle.msgs:
            print(msg)
            res = handle_req(clt, msg)
            if res:
              print(res)
              clt.conn.sendall(bytes(res))
        else:
          print(req)
          res = handle_req(clt, req)
          if res:
            print(res)
            clt.conn.sendall(bytes(res))
      else:
        print("No more data from", clt.addr)
        break
  except ModuleUnavailable as err:
    print(f"No service for module request {err}, closing")
  finally:
    clt.conn.close()
    CLIENTS.remove(clt)

def start_server():
  sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
  print(f"Proxy's wait module is listening on port {SERVER_ADDRESS[1]}")
  sock.bind(SERVER_ADDRESS)
  sock.listen(5)
    
  while True:
    clt = client.TcpClient(sock.accept())
    CLIENTS.append(clt)
    print(f"Connection from {clt.addr}")
    threading.Thread(target=handle_client, args=(clt,), daemon=True).start()

if __name__ == "__main__":
    start_server()
