# Heroes of Might and Magic V
Implementation of online game services for Heroes of Might and Magic V used for `Multi Player/Ubi.com` matchmaking (lobby list).

## Usage
Start all the services at once and stop them together with Ctrl+C (any OS):
```
python launch.py
```
On Windows, the startup script opens one window per service instead:
```
homm5.bat
```
See [Using it with the game](../README.md#using-it-with-the-game) for the ports, `servers.ini` and pointing the game at the server.

## Several games on one machine

Each game on the same machine needs its own ports. Set them in the game's `profiles/autoexec.cfg` (not `user.cfg`, which the game rewrites):
```
setvar net_game_port = 8890
setvar net_lan_match_maker_port = 8891
setvar net_ubi_cdkey_port = 42501
```

## What works
- ubi.com login with accounts: a first login creates the account. If your name is taken, you get name-1; log in with the same name and password to get the same account back.
- Lobby channels, the game list, the player list and chat.
- Creating and joining games: both players see each other in the game's slots.
- Ratings in the player list (1500 to start with), the friend list (add and remove, kept across restarts) and the Profile screen's stored data.
- Starting a game does not work yet: Start Game stays disabled ("all joined players are ready").

## Running publicly

- The services listen on all interfaces and send each game the address it reached them on. On a server with a public IP, no setting is needed.
- Behind NAT or port forwarding, set `UBIGS_HOST` to the public IP or host name, for the services and for gsconnect. `UBIGS_BIND` limits the listening address.
- Open the ports listed in [Using it with the game](../README.md#using-it-with-the-game) in the firewall.
- Players point `gsconnect.ubisoft.com` at the server (hosts file or an HTTP proxy). Their games then get every other address from `servers.ini`.
- Players' own game ports (8888 UDP by default) must be reachable for them to host games.
- Treat passwords as throwaway. The game sends them with weak, home-grown encryption, so on the network they are effectively in clear. Don't reuse a real password.
- The server stores passwords only as salted scrypt hashes.

## Data

Accounts, friends, ratings and profile data are kept in `ubigs.db` (SQLite) in the repository directory. Set `UBIGS_DB` to use another file.

`UBIGS_DB=:memory:` keeps everything in memory only; nothing is written and everything is gone when the services stop. Each service process has its own memory, so this only works when all the services run in a single process (the all-in-one launcher); with one process per service, use a file.
