import socket, sys, os, threading
# relative module import stuff
root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.append(root_dir)
import limits
import ports
import gsm, client, h5, accounts
from group import Room, LSM, MemberInfo, PLAYER_STATUS, MEMBER_NAME_SIZE
from utils import cap
from h5_data import H5_Room, H5_RoomInfo, H5_Serializer

SERVER_ADDRESS = h5.ENDPOINTS["lobby"]
"""Address of the lobby service."""

g_clients: list[client.TcpClient] = []
"""Global list of connected game clients."""

g_rooms: list[H5_Room] = []
"""Global list of active rooms (player-viewed lobbies)."""

next_room_id = 1000
"""Global room id assignment counter."""

g_matches: dict[int, dict] = {}
"""Rated matches being played: match id -> {'group': room id, 'results': {player: values}}."""

next_match_id = 1

NO_REPLY = object()
"""`handle_req` result for notifications: no reply and no echo."""

g_lock = threading.RLock()
"""Guards the global lobby state and sending to the clients."""

def send_notif(clt: client.TcpClient, subtype: gsm.LOBBY_MSG, data: list):
  """Sends a `LOBBY_MSG` notification; a client that went away is skipped."""
  header = gsm.GSMessageHeader.from_params(gsm.PROPERTY.GS, 1, gsm.MESSAGE_TYPE.LOBBY_MSG, gsm.SENDER_RECEIVER.S, gsm.SENDER_RECEIVER.P)
  msg = gsm.Message(clt.sv_bf_key, header=header, dl=gsm.List([str(subtype.value), data]))
  notif = gsm.GSMNotification(msg)
  print(f'-> {clt.username} {notif}')
  try:
    notif.send_tcp(clt)
  except OSError as err:
    print(f'failed to notify {clt.username}: {err}')

def find_room(group_id: int):
  """Returns the room with the id, if any."""
  return next((r for r in g_rooms if r.gs_room.group_id == group_id), None)

def lobby_members(lobby_id: int):
  """Clients in the lobby, including the ones in its rooms."""
  return [c for c in g_clients if c.lobby_id == lobby_id]

def room_members(room_id: int):
  """Clients in the room."""
  return [c for c in g_clients if c.room_id == room_id]

def room_list(room: H5_Room):
  """Room data list with the up to date `group_info` buffer."""
  room.gs_room.group_info = H5_Serializer().serialize_roominfo(room.room_info)
  return room.gs_room.to_list()

def shown_name(member: client.TcpClient, to: client.TcpClient = None):
  """The member's name for a player: the account name, but the typed one for the player itself (the game knows itself by it)."""
  if member is to and member.typed_name:
    return member.typed_name
  return member.username

def member_info(member: client.TcpClient, group_id: int, to: client.TcpClient = None):
  """Member data of the client in the group, for the player `to`."""
  info = MemberInfo(shown_name(member, to), str(group_id))
  info.is_visitor = member.is_visitor
  info.ip_addr = member.addr[0]
  info.alt_ip_addr = member.local_ip or member.addr[0]
  info.player_info = member.player_info
  # the game sets it only after a ladder query; until then use its entry in the room info
  room = find_room(group_id)
  if not info.player_info and room is not None:
    names = {member.username, member.typed_name}
    entry = next((p for p in room.room_info.player_infos if p.username in names), None)
    if entry is not None:
      info.player_info = H5_Serializer().serialize_playerinfo(entry)
  if find_room(group_id) is not None:
    info.status = int(PLAYER_STATUS.PS_GAMECONNECTED.value)
  else:
    info.status = 0
  return info

def send_group_info(clt: client.TcpClient, group_id: int, flags: int):
  """Sends `GROUP_INFO` of a lobby (rooms and members) or a room (room data and members)."""
  room = find_room(group_id)
  if room is not None:
    data = room_list(room)
    children = []
    members = room_members(group_id)
  else:
    data = []
    children = [room_list(r) for r in g_rooms if r.gs_room.parent_id == group_id]
    members = lobby_members(group_id)
  member_lists = [member_info(m, group_id, clt).to_list() for m in members]
  send_notif(clt, gsm.LOBBY_MSG.GROUP_INFO, [str(group_id), str(flags), data, children, member_lists])

def send_room_update(room: H5_Room, exclude: client.TcpClient = None):
  """Sends the room data (without members, they come with `MEMBER_JOIN`/`MEMBER_LEAVE`) to everyone in its lobby."""
  room_id = room.gs_room.group_id
  for member in lobby_members(room.gs_room.parent_id):
    if member is not exclude and (member.room_id is None or member.room_id == room_id):
      send_notif(member, gsm.LOBBY_MSG.GROUP_INFO, [str(room_id), str(LSM.LSM_GROUPINFO.value), room_list(room), [], []])

def send_member_join(clt: client.TcpClient, group_id: int, members: list[client.TcpClient]):
  """Sends `MEMBER_JOIN` of the client to the members."""
  info = member_info(clt, group_id)
  data = [cap(info.username, MEMBER_NAME_SIZE), "1" if info.is_visitor else "0", str(group_id), info.ip_addr, info.alt_ip_addr, info.player_info, str(info.status)]
  for member in members:
    if member is not clt:
      send_notif(member, gsm.LOBBY_MSG.MEMBER_JOIN, data)

def send_member_leave(clt: client.TcpClient, group_id: int, members: list[client.TcpClient]):
  """Sends `MEMBER_LEAVE` of the client to the members."""
  for member in members:
    if member is not clt:
      send_notif(member, gsm.LOBBY_MSG.MEMBER_LEAVE, [clt.username, str(group_id)])

def join_room_members(clt: client.TcpClient, room: H5_Room):
  """After the join reply: the room's members (the player included) to the player, the player to the members."""
  room_id = room.gs_room.group_id
  for member in room_members(room_id):
    info = member_info(member, room_id, clt)
    data = [info.username, "1" if info.is_visitor else "0", str(room_id), info.ip_addr, info.alt_ip_addr, info.player_info, str(info.status)]
    send_notif(clt, gsm.LOBBY_MSG.MEMBER_JOIN, data)
  send_member_join(clt, room_id, room_members(room_id))
  send_room_update(room, exclude=clt)

def leave_room(clt: client.TcpClient):
  """Removes the client from its room, the room goes away with its master or last player."""
  room = find_room(clt.room_id)
  clt.room_id = None
  if room is None:
    return
  room_id = room.gs_room.group_id
  if clt.is_visitor:
    room.gs_room.nb_visitors = max(0, room.gs_room.nb_visitors - 1)
  else:
    room.gs_room.nb_players = max(0, room.gs_room.nb_players - 1)
  clt.is_visitor = False
  remaining = room_members(room_id)
  if room.gs_room.master == clt.username or len(remaining) == 0:
    g_rooms.remove(room)
    for member in remaining:
      member.room_id = None
    for member in lobby_members(room.gs_room.parent_id):
      send_notif(member, gsm.LOBBY_MSG.GROUP_REMOVE, [str(room_id)])
  else:
    send_member_leave(clt, room_id, remaining)
    send_room_update(room)

def leave_lobby(clt: client.TcpClient):
  """Removes the client from its lobby (and room)."""
  leave_room(clt)
  lobby_id = clt.lobby_id
  clt.lobby_id = None
  if lobby_id is not None:
    send_member_leave(clt, lobby_id, lobby_members(lobby_id))

def new_match(group_id: int):
  """Starts a rated match of the room, returns its id."""
  global next_match_id
  match_id = next_match_id
  next_match_id += 1
  g_matches[match_id] = {'group': group_id, 'results': {}}
  return match_id

def handle_req(clt: client.TcpClient, req: gsm.Message):
  """Handler for `gsm.Message` requests."""
  global next_room_id
  res = None
  match req.header.type:
    case gsm.MESSAGE_TYPE.STILLALIVE:
      res = gsm.StillAliveResponse(req)
    case gsm.MESSAGE_TYPE.LOGINWAITMODULE:
      clt.typed_name = req.dl.lst[0]
      clt.username = accounts.account_name(clt.typed_name, clt.addr[0])
      res = gsm.LoginWaitModuleResponse(req)
    case gsm.MESSAGE_TYPE.LOBBYSERVERLOGIN:
      # the typed name; other players see the account name
      clt.typed_name = req.dl.lst[0]
      clt.username = accounts.account_name(clt.typed_name, clt.addr[0])
      # username, lobby server id, local ip, netmask
      clt.local_ip = req.dl.lst[2]
      res = gsm.LobbyServerLoginResponse(req)
    case gsm.MESSAGE_TYPE.LOBBY_MSG:
      subtype = gsm.LOBBY_MSG(int(req.dl.lst[0]))
      match subtype:
        case gsm.LOBBY_MSG.JOIN_SERVER:
          res = gsm.JoinLobbyServerResponse(req, SERVER_ADDRESS)
        case gsm.LOBBY_MSG.GROUP_INFO_GET:
          group_id = int(req.dl.lst[1][0])
          flags = int(req.dl.lst[1][1])
          # the GROUP_INFO notification is the answer
          send_group_info(clt, group_id, flags)
          res = NO_REPLY
        case gsm.LOBBY_MSG.CREATE_ROOM:
          room_id = next_room_id
          next_room_id = next_room_id + 1
          res = gsm.CreateRoomResponse(req, g_rooms, room_id, clt.username)
          room = find_room(room_id)
          room.gs_room.ip_addr = clt.addr[0]
          # NEW_GROUP notification
          for member in lobby_members(room.gs_room.parent_id):
            if member is not clt and member.room_id is None:
              send_notif(member, gsm.LOBBY_MSG.NEW_GROUP, room_list(room))
        case gsm.LOBBY_MSG.LOGIN:
          game_name = req.dl.lst[1][0]
          res = gsm.LobbyMsgResponse(req)
        case gsm.LOBBY_MSG.JOIN_LOBBY:
          lobby_id = int(req.dl.lst[1][0])
          if clt.lobby_id is not None:
            leave_lobby(clt)
          clt.lobby_id = lobby_id
          res = gsm.JoinLobbyResponse(req)
          send_member_join(clt, lobby_id, lobby_members(lobby_id))
        case gsm.LOBBY_MSG.JOIN_ROOM:
          group_id = int(req.dl.lst[1][0])
          flags = int(req.dl.lst[1][2]) # LSM (iconfig, group flags)
          room = find_room(group_id)
          if room is None:
            raise ValueError("Failed to find the requested room on join.")
          res = gsm.JoinRoomResponse(req)
          if clt.room_id is not None:
            leave_room(clt)
          clt.room_id = group_id
          clt.is_visitor = req.dl.lst[1][3] == "1"
          if clt.is_visitor:
            room.gs_room.nb_visitors += 1
          else:
            room.gs_room.nb_players += 1
          # GROUP_INFO notification; the game takes the room from it, but ignores its members until the join reply
          send_group_info(clt, group_id, flags)
          res.after = lambda: join_room_members(clt, room)
        case gsm.LOBBY_MSG.GROUP_LEAVE:
          group_id = int(req.dl.lst[1][0])
          res = gsm.GroupLeaveResponse(req)
          if group_id == clt.room_id:
            leave_room(clt)
          elif group_id == clt.lobby_id:
            leave_lobby(clt)
        case gsm.LOBBY_MSG.GROUP_CONFIG_UPDATE_RES:
          group_id = int(req.dl.lst[1][0])
          room = find_room(group_id)
          if room is None:
            raise ValueError(f'Room {group_id} not found on GROUP_CONFIG_UPDATE_RES.')
          if room.gs_room.master != clt.username:
            # only the host configures its room; the game sends this from the host only
            raise ValueError(f'{clt.username} is not the host of room {group_id}.')
          res = gsm.GroupConfigUpdateResultResponse(req, room)
          # process group_info buffer
          room.room_info = H5_Serializer().deserialize_roominfo(room.gs_room.group_info)
          # GROUP_INFO notifications
          send_room_update(room)
        case gsm.LOBBY_MSG.SET_PLAYER_INFO:
          # the game's own player data (ladder info), attached to its member info
          clt.player_info = bytes(req.dl.lst[1][0])
          res = gsm.LobbyResultResponse(req)
          for member in lobby_members(clt.lobby_id):
            if member is not clt:
              send_notif(member, gsm.LOBBY_MSG.PLAYER_INFO_UPDATE, [clt.username, clt.player_info])
        case gsm.LOBBY_MSG.START_GAME:
          # the host pressed Start: [group id]
          res = gsm.LobbyGroupResponse(req)
        case gsm.LOBBY_MSG.GAME_READY:
          # the host is ready: [group id, data, port, ?]; everyone in the room, the host included, gets GAME_STARTED
          group_id = int(req.dl.lst[1][0])
          data = req.dl.lst[1][1]
          port = req.dl.lst[1][2]
          res = gsm.LobbyGroupResponse(req)
          room = find_room(group_id)
          if room is not None:
            room.started = True
            host_ip = clt.addr[0]
            host_alt_ip = clt.local_ip or host_ip
            res.after = lambda: [send_notif(m, gsm.LOBBY_MSG.GAME_STARTED, [str(group_id), data, port, host_ip, host_alt_ip]) for m in room_members(group_id)]
        case gsm.LOBBY_MSG.START_MATCH:
          # rated game, the host starts it: [group id, ?, ?]; everyone in the room gets MATCH_STARTED with its id
          group_id = int(req.dl.lst[1][0])
          res = gsm.LobbyGroupResponse(req)
          room = find_room(group_id)
          if room is not None:
            match_id = new_match(group_id)
            room.match_id = match_id
            res.after = lambda: [send_notif(m, gsm.LOBBY_MSG.MATCH_STARTED, [str(group_id), str(match_id)]) for m in room_members(group_id)]
        case gsm.LOBBY_MSG.SUBMIT_MATCH:
          # a player's results: [match id, ?, [player, max index, count, mask, [values]], ...]
          match = g_matches.get(int(req.dl.lst[1][0]))
          if match is not None:
            for entry in req.dl.lst[1][2:]:
              if isinstance(entry, list) and len(entry) >= 5:
                match['results'][entry[0]] = entry[4]
          res = gsm.LobbyGroupResponse(req)
        case gsm.LOBBY_MSG.MATCH_FINISH:
          # [group id]; the room gets everyone's submitted results
          group_id = int(req.dl.lst[1][0])
          res = gsm.LobbyGroupResponse(req)
          room = find_room(group_id)
          match_id = room.match_id if room is not None else None
          match = g_matches.pop(match_id, None)
          if match is not None:
            room.match_id = None
            results = [[name, values] for name, values in match['results'].items()]
            final = [str(match_id), '38', results] if results else [str(match_id), '39', ['0']]
            res.after = lambda: [send_notif(m, gsm.LOBBY_MSG.FINAL_MATCH_RESULTS, final) for m in room_members(group_id)]
        case gsm.LOBBY_MSG.GAME_CONNECTED | gsm.LOBBY_MSG.GAME_FINISH | gsm.LOBBY_MSG.PLAYER_MATCH_STARTED | gsm.LOBBY_MSG.PLAYER_MATCH_FINISHED:
          # notifications, no reply
          res = NO_REPLY
        case _:
          raise NotImplementedError(f'No request handler for {subtype.name} lobby message.')
    case _:
      raise NotImplementedError(f"No request handler for {req.header.type.name} messages.")
  return res

def send_response(clt: client.TcpClient, res: gsm.GSMResponse):
  """Sends the response, then the notifications that must follow it (`res.after`)."""
  print(res)
  clt.conn.sendall(bytes(res))
  after = getattr(res, 'after', None)
  if after is not None:
    after()

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
          msgs = bundle.msgs
        else:
          msgs = [req]
        for msg in msgs:
          print(msg)
          # shared lobby state and other players' sockets
          with g_lock:
            try:
              res = handle_req(clt, msg)
            except (NotImplementedError, ValueError, IndexError, TypeError) as err:
              # an unknown or malformed request must not drop the player
              print(f"Unhandled request from {clt.username}: {err!r}")
              continue
            if res is NO_REPLY:
              pass
            elif res:
              send_response(clt, res)
      else:
        print("No more data from", clt.addr)
        break
  finally:
    clt.conn.close()
    with g_lock:
      g_clients.remove(clt)
      leave_lobby(clt)

def start_server():
  sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
  ports.bind(sock, SERVER_ADDRESS, "lobby")
  print(f"Lobby server is listening on port {SERVER_ADDRESS[1]}")
  sock.listen(5)
    
  while True:
    clt = client.TcpClient(sock.accept())
    if not limits.TCP.admit(clt):
      continue
    g_clients.append(clt)
    print(f"Connection from {clt.addr}")
    threading.Thread(target=limits.TCP.serve, args=(handle_client, clt), daemon=True).start()

if __name__ == "__main__":
    start_server()
