import copy, re, socket, sys, os, threading, time
# relative module import stuff
root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.append(root_dir)
import limits
import ports
import gsm, client, h5, accounts, ratings
from group import Room, LSM, MemberInfo, PLAYER_STATUS, MEMBER_NAME_SIZE
from utils import cap
from h5_data import H5_Room, H5_RoomInfo, H5_PlayerInfo, H5_Serializer

SERVER_ADDRESS = h5.ENDPOINTS["lobby"]
"""Address of the lobby service."""

g_clients: list[client.TcpClient] = []
"""Global list of connected game clients."""

g_rooms: list[H5_Room] = []
"""Global list of active rooms (player-viewed lobbies)."""

next_room_id = 1000
"""Global room id assignment counter."""

MAX_ROOMS = int(os.environ.get('UBIGS_MAX_ROOMS', '512'))
"""Rooms open at once (UBIGS_MAX_ROOMS); a room holds up to a message's worth of its creator's data."""

MAX_ROOM_MEMBERS = 16
"""Players and visitors of a room together (a Heroes V game has up to 8 players), so a room and its chat channel
can't gather a crowd of connections."""

g_matches: dict[int, dict] = {}
"""Rated matches being played: match id -> {'group': room id, 'results': {player: values}}."""

next_match_id = 1

NO_REPLY = object()
"""`handle_req` result for notifications: no reply and no echo."""

class Refused(ValueError):
  """A request the lobby refuses, for a reason the game shows (a `gsm.LOBBY_ERR_*` code)."""
  def __init__(self, reason: int, message: str):
    super().__init__(message)
    self.reason = reason

ANSWERED = {
  gsm.LOBBY_MSG.GROUP_LEAVE, gsm.LOBBY_MSG.CREATE_ROOM, gsm.LOBBY_MSG.START_GAME, gsm.LOBBY_MSG.START_MATCH,
  gsm.LOBBY_MSG.LOGIN, gsm.LOBBY_MSG.JOIN_LOBBY, gsm.LOBBY_MSG.JOIN_ROOM, gsm.LOBBY_MSG.SUBMIT_MATCH,
  gsm.LOBBY_MSG.GROUP_CONFIG_UPDATE_RES, gsm.LOBBY_MSG.GAME_READY, gsm.LOBBY_MSG.SET_PLAYER_INFO,
  gsm.LOBBY_MSG.MATCH_FINISH,
}
"""
The `LOBBY_MSG` requests the game sends and then waits for the reply to, in a `CStateWait*Reply` state (or, for
`LOGIN`, with nothing else to go on). Each reply decoder reads [result, [subtype, [reason, group id]]] for a refusal
(`CREATE_ROOM`: [reason, room name]), shows the reason and goes back to the lobby; without a reply the game stays on
its waiting screen for good. `GROUP_INFO_GET` is answered by a `GROUP_INFO` notification, and the rest are
notifications.
"""

def refusal(req: gsm.Message, reason: int):
  """The failure reply to a refused request the game waits for, or None."""
  if req.header.type != gsm.MESSAGE_TYPE.LOBBY_MSG:
    return None
  try:
    subtype = gsm.LOBBY_MSG(int(req.dl.lst[0]))
  except (ValueError, IndexError, TypeError):
    return None
  if subtype not in ANSWERED:
    return None
  args = req.dl.lst[1] if len(req.dl.lst) > 1 and isinstance(req.dl.lst[1], list) else []
  echo = args[0] if args and isinstance(args[0], str) else '0'
  if subtype is gsm.LOBBY_MSG.CREATE_ROOM:
    # copied into a fixed buffer
    echo = cap(args[1], gsm.LOBBY_NAME_SIZE) if len(args) > 1 and isinstance(args[1], str) else ''
  return gsm.LobbyFailResponse(req, reason, echo)

ANNOUNCE_WINDOW = 60
"""Seconds after a player left its room during which the host may still announce it."""

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

PLACEHOLDER = '0.0.0.0'
"""Stands in for an address a player must not see (#12)."""

def in_room(subject: client.TcpClient, viewer: client.TcpClient, group_id: int):
  """The viewer sees the subject from inside the same room: only there does the game need its address,
  for the peer-to-peer connection."""
  if viewer is subject:
    return True
  return viewer is not None and group_id is not None and find_room(group_id) is not None and \
    viewer.room_id == group_id and subject.room_id == group_id

def scrub_player_info(info: H5_PlayerInfo):
  """A copy of a player info without its public and private addresses."""
  out = copy.deepcopy(info)
  out.ext_ip.ip = PLACEHOLDER
  out.local_ip.ip = PLACEHOLDER
  return out

def player_data_for(data: bytes, subject: client.TcpClient, viewer: client.TcpClient, group_id: int):
  """A member's player data (`SET_PLAYER_INFO`, a `PlayerInfo` structure) as the viewer may see it."""
  if not data or in_room(subject, viewer, group_id):
    return data
  try:
    info = H5_Serializer().deserialize_playerinfo(data)
  except (ValueError, IndexError, KeyError):
    # not a structure we know: it can't be checked for addresses
    return b''
  return H5_Serializer().serialize_playerinfo(scrub_player_info(info))

def room_list(room: H5_Room, viewer: client.TcpClient = None):
  """Room data list with the up to date `group_info` buffer; the host's and players' addresses only for
  the room's members (and its host)."""
  room.gs_room.group_info = H5_Serializer().serialize_roominfo(room.room_info)
  data = room.gs_room.to_list()
  room_id = room.gs_room.group_id
  inside = viewer is not None and (viewer.room_id == room_id or viewer.username == room.gs_room.master)
  if inside:
    return data
  info = copy.deepcopy(room.room_info)
  info.host_ip.ip = PLACEHOLDER
  info.player_infos = [scrub_player_info(p) for p in info.player_infos]
  data[10] = H5_Serializer().serialize_roominfo(info)
  data[18] = PLACEHOLDER
  return data

def shown_name(member: client.TcpClient, to: client.TcpClient = None):
  """The member's name for a player: the account name, but the typed one for the player itself (the game knows itself by it)."""
  if member is to and member.typed_name:
    return member.typed_name
  return member.username

def member_info(member: client.TcpClient, group_id: int, to: client.TcpClient = None):
  """Member data of the client in the group, for the player `to`: its addresses only inside a room (#12)."""
  info = MemberInfo(shown_name(member, to), str(group_id))
  info.is_visitor = member.is_visitor
  if in_room(member, to, group_id):
    info.ip_addr = member.addr[0]
    info.alt_ip_addr = member.local_ip or member.addr[0]
  else:
    info.ip_addr = info.alt_ip_addr = PLACEHOLDER
  info.player_info = player_data_for(member.player_info, member, to, group_id)
  # the game sets it only after a ladder query; until then use its entry in the room info
  room = find_room(group_id)
  if not info.player_info and room is not None:
    names = {member.username, member.typed_name}
    entry = next((p for p in room.room_info.player_infos if p.username in names), None)
    if entry is not None:
      if not in_room(member, to, group_id):
        entry = scrub_player_info(entry)
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
    data = room_list(room, clt)
    children = []
    members = room_members(group_id)
  else:
    data = []
    children = [room_list(r, clt) for r in g_rooms if r.gs_room.parent_id == group_id]
    members = lobby_members(group_id)
  member_lists = [member_info(m, group_id, clt).to_list() for m in members]
  send_notif(clt, gsm.LOBBY_MSG.GROUP_INFO, [str(group_id), str(flags), data, children, member_lists])

def send_room_update(room: H5_Room, exclude: client.TcpClient = None):
  """Sends the room data (without members, they come with `MEMBER_JOIN`/`MEMBER_LEAVE`) to everyone in its lobby."""
  room_id = room.gs_room.group_id
  for member in lobby_members(room.gs_room.parent_id):
    if member is not exclude and (member.room_id is None or member.room_id == room_id):
      send_notif(member, gsm.LOBBY_MSG.GROUP_INFO, [str(room_id), str(LSM.LSM_GROUPINFO.value), room_list(room, member), [], []])

def send_member_join(clt: client.TcpClient, group_id: int, members: list[client.TcpClient]):
  """Sends `MEMBER_JOIN` of the client to the members, each seeing what it may (#12)."""
  for member in members:
    if member is not clt:
      info = member_info(clt, group_id, member)
      # others see the account name
      name = clt.username
      data = [cap(name, MEMBER_NAME_SIZE), "1" if info.is_visitor else "0", str(group_id), info.ip_addr, info.alt_ip_addr, info.player_info, str(info.status)]
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
    close_room(room, remaining)
  else:
    note_left(room, clt.username)
    send_member_leave(clt, room_id, remaining)
    send_room_update(room)
    publish_rooms()

def close_room(room: H5_Room, remaining: list[client.TcpClient]):
  """Removes a room; its remaining members are back in the lobby, which is told."""
  g_rooms.remove(room)
  for member in remaining:
    member.room_id = None
  for member in lobby_members(room.gs_room.parent_id):
    send_notif(member, gsm.LOBBY_MSG.GROUP_REMOVE, [str(room.gs_room.group_id)])
  publish_rooms()

def note_left(room: H5_Room, name: str):
  """Remembers that the player left the room, for the host's announcement."""
  now = time.time()
  room.left = {n: at for n, at in room.left.items() if now - at <= ANNOUNCE_WINDOW}
  room.left[name] = now

def publish_rooms():
  """
  Shares who is in each room, who is its host and who left it lately with the IRC service (another process): a
  room's host announces the players joining and leaving its room in the room's chat channel.
  """
  now = time.time()
  rows = []
  for room in g_rooms:
    room_id = room.gs_room.group_id
    for member in room_members(room_id):
      rows.append((room_id, member.username, member.typed_name or member.username,
        member.username == room.gs_room.master, None))
    for name, at in room.left.items():
      if now - at <= ANNOUNCE_WINDOW:
        rows.append((room_id, name, name, False, at))
  try:
    accounts.set_room_members(rows)
  except Exception as err:
    # only the chat announcements depend on it: it must not stop the lobby
    print(f'Room members not shared: {err!r}')

def close_unjoined(clt: client.TcpClient):
  """
  Closes the rooms the client created but isn't in. The game joins its room right after creating it, and
  `leave_room` closes a room only for a member, so these would stay for good.
  """
  for room in list(g_rooms):
    if room.creator is clt and room.gs_room.group_id != clt.room_id:
      close_room(room, room_members(room.gs_room.group_id))

def client_gone(clt: client.TcpClient):
  """Cleans up after a connection that ended."""
  g_clients.remove(clt)
  leave_lobby(clt)
  close_unjoined(clt)

def leave_lobby(clt: client.TcpClient):
  """Removes the client from its lobby (and room)."""
  leave_room(clt)
  lobby_id = clt.lobby_id
  clt.lobby_id = None
  if lobby_id is not None:
    send_member_leave(clt, lobby_id, lobby_members(lobby_id))
    publish_counts()

def publish_counts():
  """Shares the number of players in each lobby (its rooms included) with the router, whose lobby list shows them."""
  counts: dict[int, int] = {}
  for member in g_clients:
    if member.lobby_id is not None:
      counts[member.lobby_id] = counts.get(member.lobby_id, 0) + 1
  try:
    accounts.set_lobby_counts(counts)
  except Exception as err:
    # the counts are only shown: they must not stop the lobby
    print(f'Lobby counts not shared: {err!r}')

def new_match(group_id: int):
  """Starts a rated match of the room, returns its id."""
  global next_match_id
  match_id = next_match_id
  next_match_id += 1
  room = find_room(group_id)
  members = [m for m in room_members(group_id) if not m.is_visitor]
  # the names the games use for the players: account names first (a typed name may be someone else's account)
  by_name = {}
  for member in members:
    account = accounts.find(member.username)
    if account is not None:
      by_name[account.name] = account
  for member in members:
    if member.typed_name and member.typed_name not in by_name and by_name.get(member.username):
      by_name[member.typed_name] = by_name[member.username]
  g_matches[match_id] = {'group': group_id, 'results': {}, 'reports': {}, 'accounts': by_name,
    'players': [m.username for m in members], 'game': room.gs_room.game_title if room else ''}
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
          # the address the game reached (or UBIGS_HOST), not the one listened on
          res = gsm.JoinLobbyServerResponse(req, h5.advertised(SERVER_ADDRESS, clt.conn))
        case gsm.LOBBY_MSG.GROUP_INFO_GET:
          group_id = int(req.dl.lst[1][0])
          flags = int(req.dl.lst[1][1])
          # the GROUP_INFO notification is the answer
          send_group_info(clt, group_id, flags)
          res = NO_REPLY
        case gsm.LOBBY_MSG.CREATE_ROOM:
          # one room waiting for its creator to join at a time
          close_unjoined(clt)
          if len(g_rooms) >= MAX_ROOMS:
            raise Refused(gsm.LOBBY_ERR_CREATE_NOT_ALLOWED, f'CREATE_ROOM refused: {len(g_rooms)} rooms are open')
          args = req.dl.lst[1]
          if len(args) < 11:
            raise ValueError('malformed CREATE_ROOM')
          for i in (0, 3, 4, 5):
            int(args[i])
          room_id = next_room_id
          try:
            res = gsm.CreateRoomResponse(req, g_rooms, room_id, clt.username)
          except (ValueError, IndexError, KeyError, TypeError) as err:
            raise Refused(gsm.LOBBY_ERR_CREATE_NOT_ALLOWED, f'CREATE_ROOM room info: {err!r}') from err
          next_room_id = next_room_id + 1
          room = find_room(room_id)
          room.gs_room.ip_addr = clt.addr[0]
          room.creator = clt
          # NEW_GROUP notification
          for member in lobby_members(room.gs_room.parent_id):
            if member is not clt and member.room_id is None:
              send_notif(member, gsm.LOBBY_MSG.NEW_GROUP, room_list(room, member))
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
          publish_counts()
        case gsm.LOBBY_MSG.JOIN_ROOM:
          group_id = int(req.dl.lst[1][0])
          flags = int(req.dl.lst[1][2]) # LSM (iconfig, group flags)
          room = find_room(group_id)
          if room is None:
            raise Refused(gsm.LOBBY_ERR_GROUP_NOT_EXIST, f'JOIN_ROOM refused: no room {group_id}')
          if clt.room_id != group_id and len(room_members(group_id)) >= MAX_ROOM_MEMBERS:
            raise Refused(gsm.LOBBY_ERR_NO_MORE_MEMBERS, f'JOIN_ROOM refused: room {group_id} is full')
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
          publish_rooms()
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
            raise Refused(gsm.LOBBY_ERR_GROUP_NOT_EXIST, f'Room {group_id} not found on GROUP_CONFIG_UPDATE_RES.')
          if room.gs_room.master != clt.username:
            # only the host configures its room; the game sends this from the host only
            raise Refused(gsm.LOBBY_ERR_NOT_MASTER, f'{clt.username} is not the host of room {group_id}.')
          res = gsm.GroupConfigUpdateResultResponse(req, room)
          # process group_info buffer
          room.room_info = H5_Serializer().deserialize_roominfo(room.gs_room.group_info)
          # GROUP_INFO notifications
          send_room_update(room)
        case gsm.LOBBY_MSG.SET_PLAYER_INFO:
          # the game's own player data (ladder info), attached to its member info
          clt.player_info = bytes(req.dl.lst[1][0])
          res = gsm.LobbyResultResponse(req)
          # only to the client's lobby: lobby_members(None) would be every client outside a lobby
          for member in lobby_members(clt.lobby_id) if clt.lobby_id is not None else []:
            if member is not clt:
              send_notif(member, gsm.LOBBY_MSG.PLAYER_INFO_UPDATE, [clt.username, player_data_for(clt.player_info, clt, member, clt.lobby_id)])
        case gsm.LOBBY_MSG.START_GAME:
          # the host pressed Start: [group id]
          res = gsm.LobbyGroupResponse(req)
        case gsm.LOBBY_MSG.GAME_READY:
          # the host is ready: [group id, data, port, ?]; everyone in the room, the host included, gets GAME_STARTED
          group_id = int(req.dl.lst[1][0])
          data = req.dl.lst[1][1]
          port = req.dl.lst[1][2]
          room = find_room(group_id)
          if room is None:
            # a success would leave the game waiting for GAME_STARTED
            raise Refused(gsm.LOBBY_ERR_GROUP_NOT_EXIST, f'GAME_READY refused: no room {group_id}')
          res = gsm.LobbyGroupResponse(req)
          room.started = True
          host_ip = clt.addr[0]
          host_alt_ip = clt.local_ip or host_ip
          res.after = lambda: [send_notif(m, gsm.LOBBY_MSG.GAME_STARTED, [str(group_id), data, port, host_ip, host_alt_ip]) for m in room_members(group_id)]
        case gsm.LOBBY_MSG.START_MATCH:
          # rated game, the host starts it: [group id, ?, ?]; everyone in the room gets MATCH_STARTED with its id
          group_id = int(req.dl.lst[1][0])
          room = find_room(group_id)
          if room is None:
            # a success would leave the game waiting for MATCH_STARTED
            raise Refused(gsm.LOBBY_ERR_GROUP_NOT_EXIST, f'START_MATCH refused: no room {group_id}')
          res = gsm.LobbyGroupResponse(req)
          match_id = new_match(group_id)
          room.match_id = match_id
          res.after = lambda: [send_notif(m, gsm.LOBBY_MSG.MATCH_STARTED, [str(group_id), str(match_id)]) for m in room_members(group_id)]
        case gsm.LOBBY_MSG.SUBMIT_MATCH:
          # a player's results: [match id, ?, [player, max index, count, mask, [values]], ...]
          match = g_matches.get(int(req.dl.lst[1][0]))
          if match is not None:
            for entry in req.dl.lst[1][2:]:
              if isinstance(entry, list) and len(entry) >= 5:
                name, values = entry[0], entry[4]
                # every client submits every player's values; each report is kept for the ladder
                match['results'][name] = values
                match['reports'].setdefault(name, {})[clt] = values
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
            # the results into the ladder; a failure must not keep the room waiting for FINAL_MATCH_RESULTS
            try:
              # the host's client stayed to the end: its report settles a disagreement (a client that quits
              # early reports itself as the winner)
              match['finisher'] = clt
              ratings.match_finished(match)
            except Exception as err:
              print(f"Rating match {match_id} failed: {err!r}")
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

def serve_request(clt: client.TcpClient, msg: gsm.Message):
  """Handles a request and sends its reply; a request the game waits for gets its refusal if it is refused."""
  # shared lobby state and other players' sockets
  with g_lock:
    try:
      res = handle_req(clt, msg)
    except (NotImplementedError, ValueError, IndexError, TypeError) as err:
      # an unknown or malformed request must not drop the player
      print(f"Unhandled request from {clt.username}: {err!r}")
      res = refusal(msg, getattr(err, 'reason', gsm.LOBBY_ERR_UNKNOWN))
    if res is not None and res is not NO_REPLY:
      send_response(clt, res)

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
          serve_request(clt, msg)
      else:
        print("No more data from", clt.addr)
        break
  finally:
    clt.conn.close()
    with g_lock:
      client_gone(clt)

def start_server():
  sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
  ports.bind(sock, SERVER_ADDRESS, "lobby")
  # nobody is in a lobby yet: counts and rooms left by a previous run go
  publish_counts()
  publish_rooms()
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
