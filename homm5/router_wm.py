import socket, sys, os, threading
# relative module import stuff
root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.append(root_dir)
import limits
import ports
import gsm, pkc, client, group, h5, accounts

SERVER_ADDRESS = h5.ENDPOINTS["router_wm"]
"""Address of the router's wait module service."""

PROXY = h5.ENDPOINTS["proxy"]
"""Address of the proxy service the game will be redirected to."""

LOBBY_SERVER = h5.ENDPOINTS["lobby"]
"""Address of the lobby service the game will be redirected to."""

CLIENTS: list[client.TcpClient] = []
"""Global list of connected game clients."""

LOBBIES: list[group.Lobby] = [
  group.Lobby(1, "Casual", "", h5.GAME_MODE.STANDARD.value),
  group.Lobby(2, "Ranked", "", h5.GAME_MODE.RATING.value),
  group.Lobby(3, "1v1", "", h5.GAME_MODE.DUEL.value)
]
"""Global list of available server lists."""

FRIEND_UNKNOWN = 1
"""`ADDFRIEND` failure reason: no such player."""

g_lock = threading.RLock()
"""Guards sending to the clients, friend updates come from other players' threads."""

def online(account: accounts.Account):
  return any(c.account == account for c in CLIENTS)

def send_notif(clt: client.TcpClient, msg: gsm.Message):
  print(f'-> {clt.username} {msg}')
  try:
    gsm.GSMNotification(msg).send_tcp(clt)
  except OSError as err:
    print(f'failed to notify {clt.username}: {err}')

def send_friend_list(clt: client.TcpClient):
  """`UPDATEFRIEND` for each of the player's friends."""
  if clt.account is None:
    return
  for friend in accounts.friends(clt.account):
    send_notif(clt, gsm.update_friend(friend.name, online(friend)))

def notify_friend_of(clt: client.TcpClient, is_online: bool):
  """Tells the players who have this one as a friend that it came online or went offline."""
  if clt.account is None:
    return
  others = set(accounts.friend_of(clt.account))
  for other in CLIENTS:
    if other is not clt and other.account in others:
      send_notif(other, gsm.update_friend(clt.account.name, is_online))

def send_response(clt: client.TcpClient, res: gsm.GSMResponse):
  """Sends the response, then what must follow it (`res.after`)."""
  print(res)
  clt.conn.sendall(bytes(res))
  after = getattr(res, 'after', None)
  if after is not None:
    after()

def handle_req(clt: client.TcpClient, req: gsm.Message):
  """Handler for `gsm.Message` requests."""
  res = None
  match req.header.type:
    case gsm.MESSAGE_TYPE.PLAYERINFO:
      res = gsm.PlayerInfoResponse(req, clt.username)
    case gsm.MESSAGE_TYPE.STILLALIVE:
      res = gsm.StillAliveResponse(req)
    case gsm.MESSAGE_TYPE.LOGINWAITMODULE:
      # the name typed at login; the router knows its account
      clt.account = accounts.session(req.dl.lst[0], clt.addr[0])
      clt.username = clt.account.name if clt.account else req.dl.lst[0]
      res = gsm.LoginWaitModuleResponse(req)
    case gsm.MESSAGE_TYPE.LOGINFRIENDS:
      res = gsm.LoginFriendsResponse(req)
      # the friend list, and this player online for whoever has them as a friend
      res.after = lambda: (send_friend_list(clt), notify_friend_of(clt, True))
    case gsm.MESSAGE_TYPE.ADDFRIEND:
      # [name, group, ?]
      friend = accounts.find(req.dl.lst[0])
      if clt.account is None or friend is None or friend == clt.account:
        return gsm.FriendsFailResponse(req, FRIEND_UNKNOWN)
      accounts.add_friend(clt.account, friend)
      res = gsm.FriendsResponse(req, [friend.name])
      # the game adds the friend to its list on this update, not on the reply
      res.after = lambda: send_notif(clt, gsm.update_friend(friend.name, online(friend)))
    case gsm.MESSAGE_TYPE.DELFRIEND:
      # [name]
      friend = accounts.find(req.dl.lst[0])
      if clt.account is not None and friend is not None:
        accounts.remove_friend(clt.account, friend)
      res = gsm.FriendsResponse(req, [req.dl.lst[0]])
    case gsm.MESSAGE_TYPE.FRIENDLIST:
      res = gsm.FriendsResponse(req)
      res.after = lambda: send_friend_list(clt)
    case gsm.MESSAGE_TYPE.PROXY_HANDLER:
      if str(type(req.dl.lst[0])) == "<class 'list'>":
        pass
      else:
        subtype = req.dl.lst[0]
        if subtype == "1":
          module = req.dl.lst[1][0]
          match module:
            case "persistantdata":
              proxy = h5.advertised(PROXY, clt.conn)
              proxy_id = "1"
            case "ladderquery":
              proxy = h5.advertised(PROXY, clt.conn)
              proxy_id = "2"
            case "remotealgorithm":
              raise NotImplementedError(f"Remote algorithm proxy module unsupported")
            case "clanservice":
              raise NotImplementedError(f"Clan service proxy module unsupported")
            case _:
              raise NotImplementedError(f"Request for unknown proxy module {module}")
          res = gsm.ProxyHandlerResponse(req, proxy, proxy_id)
        else:
          # proxy address doesnt matter here
          res = gsm.ProxyHandlerResponse(req, h5.advertised(PROXY, clt.conn))
    case gsm.MESSAGE_TYPE.LOBBY_MSG:
      subtype = gsm.LOBBY_MSG(int(req.dl.lst[0]))
      match subtype:
        case gsm.LOBBY_MSG.JOIN_SERVER:
          res = gsm.JoinLobbyServerResponse(req, h5.advertised(LOBBY_SERVER, clt.conn))
        case gsm.LOBBY_MSG.LOGIN:
          game_name = req.dl.lst[1][0]
          res = gsm.LobbyMsgResponse(req)
        case gsm.LOBBY_MSG.CHANGE_REQUESTED_LOBBIES:
          game_name = req.dl.lst[1][0]
          res = gsm.GroupInfoResponse(req, LOBBIES)
        case _:
          raise NotImplementedError(f'No request handler for {subtype.name} lobby message.')
    case gsm.MESSAGE_TYPE.KEY_EXCHANGE:
      match req.dl.lst[0]:
        case '1':
          clt.game_pubkey = pkc.RsaPublicKey.from_buf(req.dl.lst[1][2]).to_pubkey()
          pub_key, priv_key = pkc.server_keys()
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
  reader = gsm.MessageReader()
  try:
    while True:
      data = clt.conn.recv(65536)
      if data:
        for frame in reader.feed(data):
          msg = gsm.Message(clt.sv_bf_key, in_buf=frame)
          print(msg)
          with g_lock:
            res = handle_req(clt, msg)
            if res:
              send_response(clt, res)
      else:
        print("No more data from", clt.addr)
        break
  finally:
    clt.conn.close()
    with g_lock:
      CLIENTS.remove(clt)
      if clt.account is not None and not online(clt.account):
        notify_friend_of(clt, False)

def start_server():
  sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
  ports.bind(sock, SERVER_ADDRESS, "router_wm")
  print(f"Router's wait module is listening on port {SERVER_ADDRESS[1]}")
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
