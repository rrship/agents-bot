"""Opt-in LAN monitor. Only immutable snapshots leave the Tk main thread."""
import ipaddress
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import secrets
import socket
import subprocess
import threading
import time
import tempfile
import os


PRIVATE_NETWORKS = tuple(map(ipaddress.ip_network, (
    "100.64.0.0/10", "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"
)))


def supported_address(address):
    try:
        ip = ipaddress.IPv4Address(address)
    except ValueError:
        return False
    return any(ip in network for network in PRIVATE_NETWORKS)


def local_addresses():
    try:
        addresses = {row[4][0] for row in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)}
    except OSError:
        addresses = set()
    cli = Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Tailscale/tailscale.exe"
    if cli.is_file():
        try:
            query = subprocess.run([cli, "ip", "-4"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                   timeout=2, creationflags=subprocess.CREATE_NO_WINDOW)
            addresses.update(query.stdout.decode("ascii", errors="ignore").split())
        except (OSError, subprocess.TimeoutExpired):
            pass
    return sorted(filter(supported_address, addresses), key=lambda ip: (not ip.startswith("100."), ip))


def settings_path():
    return Path(os.environ.get("LOCALAPPDATA", Path.home())) / "AgentsBot/monitor.address"


def saved_address():
    try:
        address = settings_path().read_text("ascii").strip()
    except (OSError, UnicodeError):
        return ""
    return address if supported_address(address) else ""


def remember_address(address):
    path = settings_path()
    if not address:
        path.unlink(missing_ok=True)
        return
    path.parent.mkdir(exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as output:
        output.write(address)
    os.replace(output.name, path)


class Monitor:
    def __init__(self, address, key=None, port=0):
        page = Path(__file__).with_name("mobile.html").read_bytes()
        key = key or secrets.token_urlsafe(24)
        state = self
        self.snapshot = b"{}"
        self.lock = threading.Lock()
        self.invite = None
        self.deadline = 0
        self.paired = False

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                if self.path != "/pair":
                    self.send_error(404)
                    return
                auth = self.headers.get("Authorization", "").encode()
                with state.lock:
                    granted = (state.invite and time.monotonic() < state.deadline and
                               secrets.compare_digest(auth, ("Bearer " + state.invite).encode()))
                    if granted:
                        state.invite = None
                        state.paired = True
                data = key.encode() if granted else b""
                self.send_response(200 if granted else 403)
                self.send_header("Content-Type", "text/plain; charset=ascii")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                if self.path == "/":
                    data, mime, code = page, "text/html; charset=utf-8", 200
                elif self.path == "/progress":
                    auth = self.headers.get("Authorization", "").encode()
                    if secrets.compare_digest(auth, ("Bearer " + key).encode()):
                        data, mime, code = state.snapshot, "application/json", 200
                    else:
                        data, mime, code = b"", "text/plain", 403
                else:
                    data, mime, code = b"", "text/plain", 404
                self.send_response(code)
                self.send_header("Content-Type", mime)
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("Referrer-Policy", "no-referrer")
                self.send_header("X-Frame-Options", "DENY")
                self.end_headers()
                try:
                    self.wfile.write(data)
                except ConnectionError:
                    pass

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer((address, port), Handler)
        self.link = f"http://{address}:{self.server.server_port}/#{key}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def invite_link(self):
        with self.lock:
            self.paired = False
            self.invite = secrets.token_urlsafe(24)
            self.deadline = time.monotonic() + 120
            return self.link.split("/#")[0] + "/pair#" + self.invite

    def cancel_invite(self):
        with self.lock:
            self.invite = None

    def publish(self, goal, status, members, leader, events):
        self.snapshot = json.dumps(dict(goal=goal, status=status, members=members,
                                        leader=leader, events=list(events)), ensure_ascii=False).encode()

    def close(self):
        self.cancel_invite()
        self.server.shutdown()
        self.server.server_close()


def device_key(reset=False):
    folder = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "AgentsBot"
    folder.mkdir(exist_ok=True)
    path = folder / "monitor.key"
    if reset:
        path.unlink(missing_ok=True)
    try:
        with path.open("x", encoding="ascii") as stream:
            stream.write(secrets.token_urlsafe(32))
    except FileExistsError:
        pass
    key = path.read_text(encoding="ascii").strip()
    if len(key) < 32:
        raise ValueError("设备配对记录损坏，请重置绑定")
    return key
