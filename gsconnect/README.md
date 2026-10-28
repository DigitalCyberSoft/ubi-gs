# GSConnect
GSConnect web service emulates legacy `http://gsconnect.ubisoft.com` common for GS games. Sends online configuration as `.ini` files.

## Usage
```
python gsconnect.py [--host 0.0.0.0] [--port 80] [--debug]
```
It listens on all interfaces on port 80 by default; the game's URL is hard-coded to port 80. `GSCONNECT_HOST` and `GSCONNECT_PORT` set the same options from the environment.

`--debug` turns on Flask debug mode for local testing only: its debugger runs arbitrary code, so never use it on a reachable server.

Heroes V reads the service addresses from [`static/homm5/servers.ini`](static/homm5/servers.ini). Set them to the server's public address; see [Using it with the game](../README.md#using-it-with-the-game).
