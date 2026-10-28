# Ubisoft Game Service

Game Service (GS) was an online game feature SDK developed by Ubisoft.

It allowed for implementation of user auth, friends, matchmaking, in-game chat, CD key validation and more.

The games published by Ubisoft ca. 2000-2005 used `gsconnect.ubisoft.com` for online config, along with a dedicated network protocol suite for game server communication.

## Usage

Python 3.11 or newer is required. To install all dependencies, run:
```
pip install -r requirements.txt
```

See dedicated READMEs for service usage info:

| Directory | Description |
|:-:|:-:|
| [`gsconnect`](gsconnect) | Common `gsconnect.ubisoft.com` web service |
| [`homm5`](homm5) | GS game servers for Heroes of Might and Magic V |
| [`tests`](tests) | Unit tests |

The next sections explain how to run a server for Heroes of Might and Magic V and point the game at it.

## Using it with the game

### Status
This guide describes this fork with its open pull requests merged (see the integration PR #35); where a feature comes from one PR, it is named.
- **Works:**
  - Heroes V 1.60 logs in to "ubi.com" with a per-player account (#43), stays in the lobby, and lists rooms, players and chat.
  - Joining a room fills its player slots (#39), and players on other machines can connect (#36).
  - Friends can be added and removed (#44); a friend's online state still shows greyed.
  - Ratings appear in the player list, starting at 1500 (#45), and the Profile screen loads stored data (#46).
- **Not yet:**
  - A match can't be started: the server answers the game's start requests (#39), but the host's Start Game button stays disabled, which is still being investigated (#18).
  - Because of that, rated results (#47) and saving the profile after rated play haven't been seen in the game, and results don't change ratings yet.

### 1. The server

1. Install Python 3.11+ and the dependencies (`pip install -r requirements.txt`), ideally in a virtual environment.
2. Decide the address players use, usually nothing to do. The game learns where the services are from [`gsconnect/static/homm5/servers.ini`](gsconnect/static/homm5/servers.ini), and gsconnect fills its `RouterIP0`, `IRCIP0`, `CDKeyServerIP0` and `NATServerIP0` for each request (#36):
   - `UBIGS_HOST`, if set;
   - otherwise the local address the HTTP request arrived on, i.e. the address the player reached gsconnect at;
   - otherwise the addresses in the file.

   The services likewise send the game the address it connected to when they redirect it to another service. So the file doesn't need editing. Set `UBIGS_HOST` to the public IP or hostname (for gsconnect **and** the services) when that local address isn't what players reach:
   - the server is behind NAT or port forwarding (its local address is a private one);
   - gsconnect runs behind a reverse proxy (it would see `127.0.0.1`);
   - gsconnect runs on a different machine from the services.
3. Start gsconnect. It needs port 80, because the game's URL is hard-coded; see [Running it publicly](#running-it-publicly) for running it without root:
   ```
   python gsconnect/gsconnect.py
   ```
   It listens on all interfaces (`0.0.0.0`) on port 80 by default. `--host`/`--port` or `GSCONNECT_HOST`/`GSCONNECT_PORT` change that.
4. Start the eight Heroes V services:
   ```
   python homm5/launch.py
   ```
   On Windows, `homm5\homm5.bat` opens one window per service instead. `launch.py` works on any OS: it prefixes each service's output with its name, and stops all of them on Ctrl+C or SIGTERM. If a service can't start, it says which one and why, and stops the rest (see [Troubleshooting](#troubleshooting-a-port-is-blocked)).
   - The services listen on all interfaces (`0.0.0.0`); `UBIGS_BIND` narrows that to one address (#36).
   - Player accounts are stored in `ubigs.db` next to `accounts.py`, or in the file named by `UBIGS_DB` (#43). Keep it with the server and back it up.
5. Open these ports in the firewall (and forward them, if the server is behind a router):

   | Port | Protocol | Service |
   |---|---|---|
   | 80 | TCP | gsconnect (`gsconnect/gsconnect.py`) |
   | 7777 | TCP | router (`homm5/router.py`) |
   | 7779 | TCP | IRC chat (`homm5/gsirc.py`) |
   | 7780 | UDP | CD key (`homm5/cdkey.py`) |
   | 7781 | UDP | NAT (`homm5/gsnat.py`) |
   | 7782 | TCP | router wait module (`homm5/router_wm.py`) |
   | 7783 | TCP | proxy (`homm5/proxy.py`) |
   | 7784 | TCP | proxy wait module (`homm5/proxy_wm.py`) |
   | 7785 | TCP | lobby (`homm5/lobby.py`) |

   `servers.ini` also lists `RouterLauncherPort0=7778`. No service uses it, so it doesn't need to be open.

### 2. Pointing the game at the server

The game finds the servers by requesting `http://gsconnect.ubisoft.com/gsinit.php?dp=<game id>`. It uses its bundled libcurl over plain HTTP, so it is enough to make `gsconnect.ubisoft.com` resolve to the server. No game files need to change.

**Windows:**
1. Open Notepad **as administrator**.
2. Open `C:\Windows\System32\drivers\etc\hosts` (choose "All files" in the open dialog).
3. Add a line with the server's IP address:
   ```
   203.0.113.10 gsconnect.ubisoft.com
   ```
4. Save, then run `ipconfig /flushdns` in a command prompt.

To go back to normal, remove the line.

**Linux (Wine, Proton, Lutris, Steam):**
- Add the same line to `/etc/hosts` (as root); or
- without root, start the game with an HTTP proxy pointing at gsconnect. The game's libcurl honours `http_proxy`, and gsconnect answers proxy-style requests:
  ```
  http_proxy=http://203.0.113.10:80 wine H5_Game.exe
  ```
  For Steam, put `http_proxy=http://203.0.113.10:80 %command%` in the game's launch options.

**Game versions:** gsconnect accepts the ids sent by all three games (read from their `H5_Game.exe`).

| Game | Version | `dp` sent to gsinit |
|---|---|---|
| Heroes of Might and Magic V | 1.60 | `HEROES_657d2c2ebadc6a1d` |
| Hammers of Fate | 2.1 | `HEROES_f4609f85d388479e` |
| Tribes of the East | 3.1 | `HEROES_29988429c481f219` |

Only 1.60 has been tested in-game against these services; the expansions are untested past gsinit.

**Logging in:** open Multi Player → ubi.com and type any name and password. There's no registration; the first login creates the account (#43).
- **Logging in again with the same name and password** gets the same account back, with its friends, rating and profile data.
- **A taken name:** if the name already belongs to an account with a different password, you get the next free name instead: `name-1`, `name-2`, and so on. Log in again with the same name and password and you get that same `name-k` account back.
- **Names** are case-insensitive and at most 11 bytes, suffix included; longer names are cut. A name may not be empty or contain spaces or control characters, and the password may not be empty.
- **Logging in twice:** a second login to the same account (for example after the game crashed) closes the older connection.
- **Known limitation:** the game keeps the name you typed. If you were given `alice-1`, other players see you as `alice-1`, but your own lobby list still shows you as `alice`.
- **CD key / Online Key:** any key is accepted. Ubisoft's key service is gone, so keys can't be checked.
- **Use a throwaway password, never one you use anywhere else.** The game sends it in a form anyone on the network path can read (#15).

### 3. Players behind NAT

The player's connections to the lobby, chat and NAT service are outgoing, so they shouldn't need port forwarding on the player's side (so far tested only with the game and server on one machine). Matches are peer-to-peer: players connect to the host on the game's UDP port, the console variable `net_game_port`, default **8888**.
- To change it, add `setvar net_game_port = <port>` to the game's `profiles\autoexec.cfg` (Hammers of Fate: `profiles\autoexec_a1.cfg`, Tribes of the East: `profiles\autoexec_a2.cfg`). `user.cfg` doesn't take it.
- The host may need to forward that UDP port to their PC.

Not verified yet: match start doesn't work with these services yet (#18), so whether hosting needs the forward, and whether the NAT service's address replies make it unnecessary, hasn't been tested.

### HommaRe

[HommaRe](https://github.com/livmaster-dev/HommaRe-public), a native port of the game, is getting an "ubi-gs" Multi Player entry that asks for the server address, so no hosts file or proxy is needed. That work is in progress and not merged upstream yet.

## Running it publicly

The services are public by default: they are meant to be reached by players over the internet. Before opening them up:

- **Debug mode off.** gsconnect runs without Flask debug mode unless started with `--debug`. Never use `--debug` on a reachable server: the Werkzeug debugger runs arbitrary code (#1).
- **Firewall.** Open only the ports in the table above.
- **Connection limits.** With the limits from #33, each service caps connections in total (`UBIGS_MAX_CONNECTIONS`, default 512) and per address (`UBIGS_MAX_PER_IP`, default 16). It closes connections silent for `UBIGS_SOCKET_TIMEOUT` seconds (default 180, above the game's 105 s keepalive) and caps the NAT table (`UBIGS_MAX_NAT_CLIENTS`, default 4096). A firewall rate limit on new connections (for example `nftables` `limit rate` or `iptables -m connlimit`) adds another layer.
- **Unprivileged user.** Don't run anything as root. For port 80, either:
  - give a dedicated interpreter the capability, using `--copies` so the capability doesn't apply to every Python on the system:
    ```
    python3 -m venv --copies venv
    sudo setcap cap_net_bind_service=+ep venv/bin/python3
    ```
  - or, under systemd, use `AmbientCapabilities=CAP_NET_BIND_SERVICE` in the unit;
  - or run gsconnect on a high port (`GSCONNECT_HOST=127.0.0.1 GSCONNECT_PORT=8080`) behind a reverse proxy that listens on port 80, for example nginx with `location / { proxy_pass http://127.0.0.1:8080; }` (#13). Set `UBIGS_HOST` to the public address in that case: behind the proxy gsconnect only sees `127.0.0.1` and would hand that to players.
- **Logs.** The services print every request. Passwords and CD-key data are redacted (#4), but logs still hold player names, IP addresses and chat. Rotate them, keep them private, and redact them before sharing.
- **Accounts** (#43).
  - The first login with a name creates the account; a taken name with a different password gets `name-1`, `name-2`, …, so nobody is locked out and nobody can log in to someone else's account without their password.
  - Passwords are stored only as salted scrypt hashes, in the SQLite file `ubigs.db` (or `UBIGS_DB`). Keep that file private and backed up.
  - `UBIGS_DB=:memory:` keeps accounts in memory only. That works only when all the services run in one process, not with `launch.py` or `homm5.bat`, which start one process per service.
  - The game still sends the password effectively in clear (#15), so the hash protects the stored file, not the login.
  - CD keys are never checked.

**What to tell players:**
- Use a throwaway password, never a real one. The game's protocol from 2006 has no real encryption: keyless XOR, Blowfish keys shared by every copy of the game, RSA-512 with e=3 and no TLS (#15).
- The players in your game room see your IP address: peer-to-peer matches need it. The rest of the lobby doesn't; member and room lists carry `0.0.0.0` outside the room (#12).
- Only join servers you trust. The game's network code trusts the server, and a hostile server could crash or corrupt the game client (#16, #7).

## Troubleshooting: a port is blocked

**A service won't start.** If a service can't open its port, it prints one line naming the service, the protocol and port, the reason and the fix, then exits with code 2. `homm5/launch.py` repeats that line, stops the other services and exits with the same code, so a server never runs with a service missing.

- **"another program is already using it"**: another program, or another copy of the server, holds the port. The message names the program when it can. To find it:
  - Linux: `sudo ss -ltnp 'sport = :7777'` (TCP) or `sudo ss -lunp 'sport = :7780'` (UDP)
  - macOS: `sudo lsof -nP -i TCP:7777`
  - Windows: `netstat -ano -p TCP | findstr :7777`, then `tasklist /FI "PID eq <pid>"`
- **Linux, "ports below 1024 need root"** (gsconnect on port 80): don't run the server as root. Use one of the port 80 options in [Running it publicly](#running-it-publicly): `setcap` on a `venv --copies` interpreter, systemd `AmbientCapabilities`, or a reverse proxy.
- **Windows, "Windows refused access"** (error 10013): this is the most common Windows problem.
  - **Port 80** is often held by **HTTP.sys**, which `netstat` shows as the "System" process (PID 4). It is used by IIS, the World Wide Web Publishing Service, and other HTTP.sys users. Check with:
    ```
    netsh http show servicestate
    ```
    Stop the service that uses it (for IIS: `net stop w3svc` as administrator), or run gsconnect on another machine.
  - **Any port** can fall in a range reserved by Hyper-V, WSL or Docker. Check with:
    ```
    netsh int ipv4 show excludedportrange protocol=tcp
    netsh int ipv4 show excludedportrange protocol=udp
    ```
    The reserved ranges change at boot. Running `net stop winnat` and then `net start winnat` as administrator usually frees them.
- **"the address … is not on this machine"**: the listening address (`--host`, `GSCONNECT_HOST` or the services' bind address) isn't one of this machine's addresses. Use `0.0.0.0`.

**The server runs, but players can't connect.** Binding can work while the host firewall still drops incoming traffic. After starting, the server checks the firewall (best effort, without changing anything):

- **Windows:** it reads the Windows Defender Firewall rules (`netsh advfirewall firewall show rule name=all dir=in verbose`).
  - If no enabled inbound rule allows a port, or one blocks it, it prints a prominent warning. Windows creates a blocking rule for `python.exe` when its "allow access" prompt is cancelled.
  - The warning lists the ports and the commands to open them. Run those as administrator; for all ports they are:
    ```
    netsh advfirewall firewall add rule name="ubi-gs TCP" dir=in action=allow protocol=TCP localport=80,7777,7779,7782,7783,7784,7785
    netsh advfirewall firewall add rule name="ubi-gs UDP" dir=in action=allow protocol=UDP localport=7780,7781
    ```
- **Linux:** a hint, never a failure.
  - **firewalld or ufw active:** if the ports aren't open, it prints one line, for example `sudo firewall-cmd --permanent --add-port=7777/tcp … && sudo firewall-cmd --reload` or `sudo ufw allow 7777/tcp`.
  - **Neither active:** it reads the plain iptables/nftables rules (`iptables -S`, `nft list ruleset`).
    - These usually need root. Without it, it reads a saved ruleset (`/etc/sysconfig/iptables`, `/etc/iptables/rules.v4`, `/etc/nftables.conf`, `/etc/sysconfig/nftables.conf`) if that is readable.
    - Otherwise it says the check needs root, so you can run it yourself:
      ```
      sudo iptables -S INPUT
      sudo nft list ruleset
      ```
    - If the input chain drops traffic by default (policy DROP, or a final catch-all `REJECT`/`drop`) and no rule accepts the server's ports, it prints rules to add, for example:
      ```
      sudo iptables -I INPUT -p tcp -m multiport --dports 80,7777,7779,7782,7783,7784,7785 -j ACCEPT
      sudo iptables -I INPUT -p udp -m multiport --dports 7780,7781 -j ACCEPT
      sudo nft add rule inet filter input tcp dport { 80, 7777, 7779, 7782, 7783, 7784, 7785 } accept
      sudo nft add rule inet filter input udp dport { 7780, 7781 } accept
      ```
      Save them the way your distribution does, e.g. `iptables-save` or `/etc/nftables.conf`.

The check is a warning only; the server keeps running. A router in front of the server still needs the same ports forwarded to it.

## Disclaimers

This project is not maintained by or affiliated with Ubisoft or Nival (formerly Nival Interactive).

All emulated services and tools were developed using techniques of software reverse engineering, on the basis of Ubisoft's end-of-life [announcement](https://www.ubisoft.com/en-us/help/purchases-and-rewards/article/decommissioning-of-online-services-for-older-legacy-ubisoft-games-a-m/000064576) for the game's online services, which implies termination of the [EULA](https://www.ubisoft.com/legal/documents/eula/en-US).

All rights belong to their respective owners.

## Games
An incomplete list of titles using GS SDK:
- Heroes of Might and Magic V
- Tom Clancy's Splinter Cell: Chaos Theory
- Brothers in Arms: Road to Hill 30
