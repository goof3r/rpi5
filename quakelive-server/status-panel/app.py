import json, mimetypes, os, re, socket, struct, sys, zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote

APP_DIR = Path(__file__).parent
QLDS_DIR = Path(os.environ.get("QLDS_DIR", str(Path.home() / "qlds")))
CACHE_DIR = APP_DIR / "cache"
CACHE_DIR.mkdir(exist_ok=True)
SERVERS = json.loads((APP_DIR / "servers.json").read_text(encoding="utf-8"))
TEMPLATE = (APP_DIR / "templates" / "index.html").read_bytes()

A2S_INFO_PKT = b"\xff\xff\xff\xffTSource Engine Query\x00"
A2S_PLAYER_CHALLENGE = b"\xff\xff\xff\xff\x55\xff\xff\xff\xff"
PREFIX = b"\xff\xff\xff\xff"
NAME_RE = re.compile(r"^[a-z0-9_\-]{1,64}$")
STATIC_RE = re.compile(r"^/static/([a-zA-Z0-9_./-]+)$")
LEVELSHOT_RE = re.compile(r"^/levelshot/([^/]+)\.jpg$")


def _read_cstr(buf, i):
    end = buf.index(b"\x00", i)
    return buf[i:end].decode("utf-8", errors="replace"), end + 1


def a2s_info(host, port, timeout=1.2):
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.settimeout(timeout)
        try:
            s.sendto(A2S_INFO_PKT, (host, port))
            data, _ = s.recvfrom(4096)
            if data.startswith(PREFIX + b"A") and len(data) >= 9:
                s.sendto(A2S_INFO_PKT + data[5:9], (host, port))
                data, _ = s.recvfrom(4096)
        except (socket.timeout, OSError):
            return None
    if not data.startswith(PREFIX + b"I"):
        return None
    try:
        i = 6  # \xff\xff\xff\xff + 'I' + protocol byte
        name, i = _read_cstr(data, i)
        mapname, i = _read_cstr(data, i)
        _folder, i = _read_cstr(data, i)
        game, i = _read_cstr(data, i)
        i += 2
        players = data[i]; i += 1
        maxplayers = data[i]; i += 1
        bots = data[i]; i += 1
    except (IndexError, ValueError):
        return None
    return {"name": name, "map": mapname.lower(), "gametype": game,
            "players": players, "maxplayers": maxplayers, "bots": bots}


def a2s_players(host, port, timeout=0.8):
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.settimeout(timeout)
        try:
            s.sendto(A2S_PLAYER_CHALLENGE, (host, port))
            data, _ = s.recvfrom(4096)
            if data[4:5] == b"A" and len(data) >= 9:
                s.sendto(b"\xff\xff\xff\xff\x55" + data[5:9], (host, port))
                data, _ = s.recvfrom(4096)
        except (socket.timeout, OSError):
            return []
    if not data.startswith(PREFIX + b"D"):
        return []
    out = []
    try:
        i = 5
        n = data[i]; i += 1
        for _ in range(n):
            i += 1
            name, i = _read_cstr(data, i)
            score = struct.unpack_from("<i", data, i)[0]; i += 4
            duration = struct.unpack_from("<f", data, i)[0]; i += 4
            if name:
                out.append({"name": name, "frags": score, "duration": int(max(0, duration))})
    except (IndexError, struct.error, ValueError):
        pass
    return out


QL_APPID = "282440"


def status_payload():
    out = []
    for s in SERVERS:
        query_host = s.get("queryHost", s["host"])
        connect = f"steam://rungameid/{QL_APPID}//+connect%20{s['host']}:{s['port']}"
        info = a2s_info(query_host, s["port"])
        if info:
            players = a2s_players(query_host, s["port"])
            players.sort(key=lambda p: -p["frags"])
            out.append({
                "id": s["id"], "name": info["name"] or s["name"],
                "host": s["host"], "port": s["port"], "connect": connect,
                "online": True,
                "map": info["map"], "gametype": info["gametype"],
                "playerCount": info["players"], "bots": info["bots"],
                "humans": max(0, info["players"] - info["bots"]),
                "maxPlayers": info["maxplayers"], "players": players,
            })
        else:
            out.append({"id": s["id"], "name": s["name"],
                        "host": s["host"], "port": s["port"], "connect": connect,
                        "online": False, "map": "", "gametype": "",
                        "playerCount": 0, "bots": 0, "humans": 0,
                        "maxPlayers": 0, "players": []})
    return out


def _scan_pk3s():
    paths = []
    for sub in ["baseq3", "instances/*/baseq3"]:
        paths.extend(QLDS_DIR.glob(f"{sub}/*.pk3"))
    for d in QLDS_DIR.glob("steamapps/workshop/content/*"):
        paths.extend(d.rglob("*.pk3"))
    return paths


def extract_levelshot(mapname, dest):
    targets_lc = {f"levelshots/{mapname}.jpg".lower()}
    for pk3 in _scan_pk3s():
        try:
            with zipfile.ZipFile(pk3) as z:
                for n in z.namelist():
                    if n.lower() in targets_lc:
                        dest.write_bytes(z.read(n))
                        return True
        except (zipfile.BadZipFile, OSError):
            continue
    return False


class Handler(BaseHTTPRequestHandler):
    server_version = "qlstatus/1.0"

    def log_message(self, fmt, *args):
        sys.stdout.write(f"{self.address_string()} - {fmt % args}\n")
        sys.stdout.flush()

    def _send(self, status, body, ctype, headers=None):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store" if status == 200 and ctype.startswith("application/json") else "public, max-age=30")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, path: Path, ctype=None):
        if not path.is_file():
            return self._send(404, b"not found", "text/plain")
        data = path.read_bytes()
        if ctype is None:
            ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        self._send(200, data, ctype)

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path in ("/", "/index.html"):
            return self._send(200, TEMPLATE, "text/html; charset=utf-8")
        if path == "/api/status":
            body = json.dumps(status_payload(), ensure_ascii=False).encode("utf-8")
            return self._send(200, body, "application/json; charset=utf-8")
        if path == "/api/servers":
            body = json.dumps(SERVERS, ensure_ascii=False).encode("utf-8")
            return self._send(200, body, "application/json; charset=utf-8")
        m = LEVELSHOT_RE.match(path)
        if m:
            mapname = unquote(m.group(1)).lower()
            if not NAME_RE.match(mapname):
                return self._send(404, b"not found", "text/plain")
            cached = CACHE_DIR / f"{mapname}.jpg"
            if cached.exists():
                return self._send_file(cached, "image/jpeg")
            if extract_levelshot(mapname, cached):
                return self._send_file(cached, "image/jpeg")
            return self._send_file(APP_DIR / "static" / "placeholder.svg", "image/svg+xml")
        m = STATIC_RE.match(path)
        if m and ".." not in m.group(1):
            return self._send_file(APP_DIR / "static" / m.group(1))
        return self._send(404, b"not found", "text/plain")


def main():
    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "80"))
    httpd = ThreadingHTTPServer((host, port), Handler)
    print(f"qlstatus listening on http://{host}:{port}/", flush=True)
    httpd.serve_forever()


if __name__ == "__main__":
    main()
