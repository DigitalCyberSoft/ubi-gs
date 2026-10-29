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
- Friends: add and remove them in the lobby, see who is online. The list is kept across restarts.
- Ratings in the player list (1500 to start with).
- Creating, joining and starting games, unrated (Casual) and rated (Ranked), and playing them on the map. A rated game's results go back to the players when it ends.
- Rated results update the ladder: Elo ratings (from 1500) and the per-race statistics, from the values the games submit (#61). The game reads its own rating for the player list at login, so a new rating shows there after the next login.
- The Profile screen (select a player in the lobby, then Profile): ratings, games and races from the ladder, and the player's own "Additional Information" text. A player who never saved one gets an empty profile. The game saves its text when you change it and leave the screen; the server keeps it per account, and anyone can view it.

## Starting a game

In the game's room ("Standard options"), every player has to mark themselves ready before the host can press Start Game:
1. In your own row, click the small arrow to the right of the round state icon (tooltip "Set current player state"). The icon changes to a brain with a tick, and the other players see it too.
2. Once every joined player, the host included, shows the tick, the host's Start Game button turns on.

Clicking the arrow again takes the mark back. Readiness goes straight between the games over the host's game port (UDP 8888 by default), not through these services, so the host's game port must be reachable by the other players.

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

`UBIGS_DB=:memory:` keeps everything in memory only; nothing is written and everything is gone when the services stop. Each service process has its own memory, so this only works once all the services run in a single process; `launch.py` and `homm5.bat` still start one process per service, so use a file with them.
