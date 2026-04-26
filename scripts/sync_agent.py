#!/usr/bin/env python3
"""LAN discovery and peer-to-peer transfer agent for Codex continuity.

This is the user-friendly layer above `continuity_pack.py`: machines on the
same local network announce themselves, filter peers by the same account
fingerprint, and exchange continuity packs through a tiny HTTP API.
"""

from __future__ import annotations

import argparse
import contextlib
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import socket
import tempfile
import threading
import time
import urllib.parse
import uuid

import continuity_pack


PLUGIN_NAME = "codex-continuity-sync"
MULTICAST_GROUP = "239.77.77.77"
DISCOVERY_PORT = 47777
ANNOUNCE_INTERVAL_SECONDS = 5
PEER_TTL_SECONDS = 20


def now() -> float:
    return time.time()


def read_json(path: Path, default: object) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return default


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True), encoding="utf-8")


def state_dir(codex_home: Path) -> Path:
    return codex_home / "continuity-sync"


def load_device(codex_home: Path) -> dict:
    path = state_dir(codex_home) / "device.json"
    value = read_json(path, {})
    if not isinstance(value, dict):
        value = {}
    changed = False
    if not value.get("deviceId"):
        value["deviceId"] = str(uuid.uuid4())
        changed = True
    if not value.get("deviceName"):
        value["deviceName"] = socket.gethostname() or "Codex machine"
        changed = True
    if changed:
        write_json(path, value)
    return value


def peer_store_path(codex_home: Path) -> Path:
    return state_dir(codex_home) / "peers.json"


class PeerStore:
    def __init__(self, codex_home: Path, self_device_id: str, account_fingerprint: str | None):
        self.codex_home = codex_home
        self.self_device_id = self_device_id
        self.account_fingerprint = account_fingerprint
        self.lock = threading.Lock()
        self.peers: dict[str, dict] = {}

    def update(self, peer: dict, host: str) -> None:
        if peer.get("plugin") != PLUGIN_NAME:
            return
        if peer.get("deviceId") == self.self_device_id:
            return
        if not self.account_fingerprint:
            return
        if peer.get("accountFingerprint") != self.account_fingerprint:
            return
        peer = dict(peer)
        peer["host"] = host
        peer["lastSeen"] = now()
        with self.lock:
            self.peers[peer["deviceId"]] = peer
            self._flush_locked()

    def list(self) -> list[dict]:
        cutoff = now() - PEER_TTL_SECONDS
        with self.lock:
            self.peers = {
                device_id: peer
                for device_id, peer in self.peers.items()
                if peer.get("lastSeen", 0) >= cutoff
            }
            self._flush_locked()
            return sorted(self.peers.values(), key=lambda peer: peer.get("deviceName", ""))

    def get(self, device_id: str) -> dict | None:
        for peer in self.list():
            if peer.get("deviceId") == device_id:
                return peer
        return None

    def _flush_locked(self) -> None:
        write_json(peer_store_path(self.codex_home), {"peers": list(self.peers.values())})


def local_ip_hint() -> str:
    with contextlib.closing(socket.socket(socket.AF_INET, socket.SOCK_DGRAM)) as sock:
        try:
            sock.connect(("8.8.8.8", 80))
            return sock.getsockname()[0]
        except OSError:
            return "127.0.0.1"


def build_announcement(device: dict, binding: dict, api_port: int) -> bytes:
    return json.dumps(
        {
            "type": "announce",
            "plugin": PLUGIN_NAME,
            "deviceId": device["deviceId"],
            "deviceName": device["deviceName"],
            "accountFingerprint": binding.get("fingerprint"),
            "accountBindingMode": binding.get("mode"),
            "apiPort": api_port,
            "version": 1,
        },
        sort_keys=True,
    ).encode("utf-8")


def announce_loop(stop: threading.Event, payload: bytes) -> None:
    with contextlib.closing(socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)) as sock:
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
        while not stop.is_set():
            with contextlib.suppress(OSError):
                sock.sendto(payload, (MULTICAST_GROUP, DISCOVERY_PORT))
            stop.wait(ANNOUNCE_INTERVAL_SECONDS)


def listen_loop(stop: threading.Event, peers: PeerStore) -> None:
    with contextlib.closing(socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("", DISCOVERY_PORT))
        membership = socket.inet_aton(MULTICAST_GROUP) + socket.inet_aton("0.0.0.0")
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, membership)
        sock.settimeout(1)
        while not stop.is_set():
            try:
                data, address = sock.recvfrom(65535)
            except socket.timeout:
                continue
            except OSError:
                break
            try:
                message = json.loads(data.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
            if isinstance(message, dict):
                peers.update(message, address[0])


def make_handler(codex_home: Path, account_id: str | None, peers: PeerStore):
    class Handler(BaseHTTPRequestHandler):
        server_version = "CodexContinuitySync/0.1"

        def do_GET(self) -> None:
            parsed = urllib.parse.urlparse(self.path)
            if parsed.path == "/health":
                self.write_json({"ok": True, "plugin": PLUGIN_NAME})
            elif parsed.path == "/peers":
                self.write_json({"peers": peers.list()})
            elif parsed.path == "/pack":
                self.write_pack()
            else:
                self.send_error(404)

        def do_POST(self) -> None:
            parsed = urllib.parse.urlparse(self.path)
            if parsed.path == "/pull":
                query = urllib.parse.parse_qs(parsed.query)
                device_id = (query.get("deviceId") or [""])[0]
                peer = peers.get(device_id)
                if not peer:
                    self.send_error(404, "Peer not found")
                    return
                try:
                    imported = pull_from_peer(peer, codex_home, account_id)
                except RuntimeError as exc:
                    self.send_error(502, str(exc))
                    return
                self.write_json({"ok": True, "imported": imported, "peer": peer})
            else:
                self.send_error(404)

        def write_pack(self) -> None:
            with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp:
                tmp_path = Path(tmp.name)
            try:
                args = argparse.Namespace(
                    source_codex_home=str(codex_home),
                    output=str(tmp_path),
                    account_id=account_id,
                    include_secrets=False,
                )
                result = continuity_pack.write_export(args)
                if result:
                    self.send_error(500, "Could not create continuity pack")
                    return
                data = tmp_path.read_bytes()
            finally:
                with contextlib.suppress(OSError):
                    tmp_path.unlink()
            self.send_response(200)
            self.send_header("Content-Type", "application/zip")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def write_json(self, value: object) -> None:
            body = json.dumps(value, indent=2, sort_keys=True).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:
            return

    return Handler


def pull_from_peer(peer: dict, codex_home: Path, account_id: str | None) -> int:
    host = peer["host"]
    port = int(peer["apiPort"])
    conn = http.client.HTTPConnection(host, port, timeout=30)
    with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp:
        tmp_path = Path(tmp.name)
    try:
        conn.request("GET", "/pack")
        response = conn.getresponse()
        if response.status != 200:
            raise RuntimeError(f"Peer returned HTTP {response.status}")
        tmp_path.write_bytes(response.read())
        args = argparse.Namespace(
            pack=str(tmp_path),
            target_codex_home=str(codex_home),
            account_id=account_id,
            allow_unknown_account=False,
            backup=True,
        )
        return continuity_pack.write_import(args)
    finally:
        conn.close()
        with contextlib.suppress(OSError):
            tmp_path.unlink()


def run_server(args: argparse.Namespace) -> int:
    codex_home = Path(args.codex_home).expanduser().resolve()
    codex_home.mkdir(parents=True, exist_ok=True)
    device = load_device(codex_home)
    binding = continuity_pack.account_binding(codex_home, args.account_id)
    if not binding.get("fingerprint"):
        print("Account could not be inferred. Start with --account-id for same-account discovery.")
    peers = PeerStore(codex_home, device["deviceId"], binding.get("fingerprint"))
    server = ThreadingHTTPServer((args.host, args.port), make_handler(codex_home, args.account_id, peers))
    api_port = server.server_address[1]
    payload = build_announcement(device, binding, api_port)
    stop = threading.Event()
    threads = [
        threading.Thread(target=announce_loop, args=(stop, payload), daemon=True),
        threading.Thread(target=listen_loop, args=(stop, peers), daemon=True),
    ]
    for thread in threads:
        thread.start()
    print(f"{PLUGIN_NAME} agent running for {device['deviceName']} at http://{local_ip_hint()}:{api_port}")
    print("Peers with the same account and plugin will appear automatically.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        server.server_close()
    return 0


def list_peers(args: argparse.Namespace) -> int:
    codex_home = Path(args.codex_home).expanduser().resolve()
    value = read_json(peer_store_path(codex_home), {"peers": []})
    print(json.dumps(value, indent=2, sort_keys=True))
    return 0


def pull_command(args: argparse.Namespace) -> int:
    codex_home = Path(args.codex_home).expanduser().resolve()
    value = read_json(peer_store_path(codex_home), {"peers": []})
    peers = value.get("peers", []) if isinstance(value, dict) else []
    peer = next((item for item in peers if item.get("deviceId") == args.device_id), None)
    if not peer:
        print(f"Peer not found: {args.device_id}")
        return 2
    return pull_from_peer(peer, codex_home, args.account_id)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    serve = sub.add_parser("serve", help="Run discovery and transfer agent")
    serve.add_argument("--codex-home", default=str(continuity_pack.codex_home_from_env()))
    serve.add_argument("--account-id", help="Stable account identifier for same-account discovery")
    serve.add_argument("--host", default="0.0.0.0")
    serve.add_argument("--port", type=int, default=0)
    serve.set_defaults(func=run_server)

    peers = sub.add_parser("peers", help="Show recently discovered same-account peers")
    peers.add_argument("--codex-home", default=str(continuity_pack.codex_home_from_env()))
    peers.set_defaults(func=list_peers)

    pull = sub.add_parser("pull", help="Pull continuity from a discovered peer")
    pull.add_argument("device_id")
    pull.add_argument("--codex-home", default=str(continuity_pack.codex_home_from_env()))
    pull.add_argument("--account-id", help="Stable account identifier used to verify this is the same account")
    pull.set_defaults(func=pull_command)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
