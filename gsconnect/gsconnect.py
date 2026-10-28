import argparse, os, re
from flask import Flask, request, send_from_directory, abort, Response

app = Flask(__name__)

# config for game file directories, next to this script
static_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static')
app.config['HOMM5'] = os.path.join(static_dir, 'homm5')
app.config['SP3'] = os.path.join(static_dir, 'sp3')

@app.route('/<php_filename>', methods=['GET'])
def get(php_filename: str) -> Response:
    """The initial request for game server discovery. Returns an `.ini` file with available services."""
    # dp parameter - game key/identifier
    dp = request.args.get('dp')
    game = None
    ini_file = None
    if dp is None:
        return 'Missing game id', 400

    # remove HoMM5 hash
    if re.search("^HEROES_[a-f0-9]{16}$", dp) is not None:
        dp = "HEROES_5"

    if php_filename=='gsinit.php':
        match dp:
            # Heroes of Might and Magic V + DLCs
            case 'HEROES_5':
                game = 'HOMM5'
                ini_file = 'servers.ini'
                
            # Tom Clancy’s Splinter Cell: Chaos Theory
            case 'SPLINTERCELL3PS2US':
                game = 'SP3'
                ini_file = 'GS.ini'
                # the game supplies 'user' parameter, e.g. 'noname'
                user = dp = request.args.get('user')

            case 'SPLINTERCELL3PC':
                game = 'SP3'
                ini_file = 'GS.ini'
                user = dp = request.args.get('user')

            case 'SPLINTERCELL3PCCOOP':
                game = 'SP3'
                ini_file = 'GS.ini'
                user = dp = request.args.get('user')

            case 'SPLINTERCELL3PCADVERS':
                game = 'SP3'
                ini_file = 'GS.ini'
                user = dp = request.args.get('user')

            # Unknown
            case _:
                print('Unknown game id')
                return 'Unknown game id', 400
        host = services_host()
        if game == 'HOMM5' and host:
            # the game services' address: wherever this request arrived, as the services do (homm5/h5.py)
            try:
                with open(os.path.join(app.config[game], ini_file), encoding='ascii') as file:
                    ini = re.sub(r'(?m)^(\w+IP\d+)=.*$', lambda m: f'{m.group(1)}={host}', file.read())
            except FileNotFoundError:
                abort(404)
            return Response(ini, mimetype='application/octet-stream', headers={'Content-Disposition': f'attachment; filename={ini_file}'})
        try:
            return send_from_directory(directory=app.config[game], path=ini_file, as_attachment=True)
        except FileNotFoundError:
            abort(404)
    else:
        print(f'Unknown request for {php_filename}')
        return 'Unknown request', 400

def services_host():
    """UBIGS_HOST, else the local address the request arrived on; None keeps the file's addresses."""
    host = os.environ.get('UBIGS_HOST')
    if host:
        return host
    sock = request.environ.get('werkzeug.socket')
    if sock is not None:
        try:
            return sock.getsockname()[0]
        except OSError:
            pass
    return None

def get_args():
    parser = argparse.ArgumentParser(description='gsconnect.ubisoft.com web service')
    parser.add_argument('--host', default=os.environ.get('GSCONNECT_HOST', '0.0.0.0'),
        help='address to listen on (default 0.0.0.0, env GSCONNECT_HOST)')
    parser.add_argument('--port', type=int, default=int(os.environ.get('GSCONNECT_PORT', '80')),
        help='port to listen on (default 80, env GSCONNECT_PORT)')
    # never in public: the debugger console runs code
    parser.add_argument('--debug', action='store_true', help='Flask debug mode, local testing only')
    return parser.parse_args()

if __name__ == '__main__':
    args = get_args()
    app.run(host=args.host, port=args.port, debug=args.debug)
