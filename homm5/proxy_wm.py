import socket, sys, os, threading
# relative module import stuff
root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.append(root_dir)
import gsm, pkc, client, h5, accounts, ladder

SERVER_ADDRESS = h5.ENDPOINTS["proxy_wm"]
"""Address of the proxy's wait module service."""

CLIENTS: list[client.TcpClient] = []
"""Global list of connected game clients."""

class ModuleUnavailable(Exception):
  """A proxy module request this server has no service for."""

MODULES = {
  ladder.QUERY: ladder.query,
}
"""Module data handlers by code: (client, payload) -> result list ['38'|'39', data]."""

def module_response(clt: client.TcpClient, req: gsm.Message):
  """Answers module data `[code, seq, payload]` on the same connection: [code, seq, result] (0x0043f790).
  The game matches the reply by `seq`."""
  code, seq, payload = req.dl.lst[0], req.dl.lst[1], req.dl.lst[2]
  handler = MODULES.get(code)
  if handler is None:
    raise ModuleUnavailable(code)
  res = gsm.GSMResponse(req)
  res.dl = gsm.List([code, seq, handler(clt, payload)])
  return res

def handle_req(clt: client.TcpClient, req: gsm.Message):
  """Handler for `gsm.Message` requests."""
  res = None
  match req.header.type:
    case gsm.MESSAGE_TYPE.STILLALIVE:
      res = gsm.StillAliveResponse(req)
    case gsm.MESSAGE_TYPE.LOGINWAITMODULE:
      # the name typed at login; the router knows its account
      clt.username = accounts.account_name(req.dl.lst[0], clt.addr[0])
      res = gsm.ProxyLoginWaitModuleResponse(req)
    case gsm.MESSAGE_TYPE.PROXY_HANDLER:
      # module data; for others, closing the module's connection fails them and the game carries on
      res = module_response(clt, req)
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
            elif req.header.type != gsm.MESSAGE_TYPE.STILLALIVE:
              clt.conn.sendall(data)
        else:
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
