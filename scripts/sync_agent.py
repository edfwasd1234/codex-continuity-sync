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
import webbrowser

import continuity_pack


PLUGIN_NAME = "codex-continuity-sync"
MULTICAST_GROUP = "239.77.77.77"
DISCOVERY_PORT = 47777
ANNOUNCE_INTERVAL_SECONDS = 5
PEER_TTL_SECONDS = 20

APP_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Codex Continuity Sync</title>
  <style>
    :root {
      color-scheme: light;
      --bg: #f7f8fa;
      --panel: #ffffff;
      --ink: #17191f;
      --muted: #69707d;
      --line: #dfe3ea;
      --blue: #2563eb;
      --blue-strong: #1d4ed8;
      --green: #16794c;
      --red: #b42318;
      --shadow: 0 16px 40px rgba(20, 28, 45, 0.08);
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font: 14px/1.45 "Segoe UI", system-ui, -apple-system, BlinkMacSystemFont, sans-serif;
      color: var(--ink);
      background: var(--bg);
    }
    main {
      max-width: 1080px;
      margin: 0 auto;
      padding: 28px;
    }
    header {
      display: flex;
      align-items: flex-start;
      justify-content: space-between;
      gap: 20px;
      padding: 8px 0 24px;
      border-bottom: 1px solid var(--line);
    }
    h1 {
      margin: 0;
      font-size: 28px;
      line-height: 1.15;
      font-weight: 700;
      letter-spacing: 0;
    }
    .subhead {
      margin-top: 7px;
      color: var(--muted);
      max-width: 680px;
    }
    .status-pill {
      display: inline-flex;
      align-items: center;
      gap: 8px;
      min-height: 34px;
      padding: 6px 12px;
      border: 1px solid var(--line);
      background: var(--panel);
      border-radius: 999px;
      white-space: nowrap;
      box-shadow: 0 8px 24px rgba(20, 28, 45, 0.05);
    }
    .dot {
      width: 8px;
      height: 8px;
      border-radius: 999px;
      background: var(--green);
    }
    .setup {
      display: none;
      margin-top: 24px;
      padding: 18px;
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      box-shadow: var(--shadow);
    }
    .setup.show { display: block; }
    .setup-form {
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto;
      gap: 10px;
      margin-top: 14px;
    }
    input {
      width: 100%;
      min-height: 38px;
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 8px 10px;
      font: inherit;
    }
    .grid {
      display: grid;
      grid-template-columns: minmax(0, 1fr) 320px;
      gap: 22px;
      padding-top: 24px;
    }
    section, aside {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      box-shadow: var(--shadow);
    }
    .section-head {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
      padding: 18px 18px 14px;
      border-bottom: 1px solid var(--line);
    }
    h2 {
      margin: 0;
      font-size: 16px;
      line-height: 1.25;
      font-weight: 650;
      letter-spacing: 0;
    }
    .muted { color: var(--muted); }
    button {
      border: 1px solid var(--line);
      background: var(--panel);
      color: var(--ink);
      min-height: 36px;
      padding: 8px 12px;
      border-radius: 6px;
      font: inherit;
      cursor: pointer;
    }
    button:hover { border-color: #c7ced9; background: #fbfcfe; }
    button.primary {
      border-color: var(--blue);
      background: var(--blue);
      color: white;
      font-weight: 650;
    }
    button.primary:hover { background: var(--blue-strong); }
    button:disabled {
      opacity: 0.58;
      cursor: not-allowed;
    }
    .peer-list {
      display: grid;
      gap: 12px;
      padding: 16px;
    }
    .peer {
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto;
      gap: 14px;
      align-items: center;
      padding: 14px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #fcfdff;
    }
    .peer-name {
      font-size: 15px;
      font-weight: 650;
      overflow-wrap: anywhere;
    }
    .peer-meta {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      margin-top: 7px;
      color: var(--muted);
      font-size: 12px;
    }
    .tag {
      border: 1px solid var(--line);
      border-radius: 999px;
      padding: 2px 8px;
      background: white;
    }
    .empty {
      padding: 42px 18px;
      color: var(--muted);
      text-align: center;
    }
    aside {
      padding: 18px;
      align-self: start;
    }
    .info {
      display: grid;
      gap: 12px;
      margin-top: 14px;
    }
    .info-row {
      display: grid;
      gap: 3px;
      padding-bottom: 12px;
      border-bottom: 1px solid var(--line);
    }
    .info-row:last-child {
      border-bottom: 0;
      padding-bottom: 0;
    }
    .label {
      color: var(--muted);
      font-size: 12px;
    }
    .value {
      font-family: ui-monospace, "Cascadia Code", Consolas, monospace;
      font-size: 12px;
      overflow-wrap: anywhere;
    }
    .toast {
      position: fixed;
      right: 22px;
      bottom: 22px;
      width: min(420px, calc(100vw - 44px));
      padding: 14px 16px;
      border-radius: 8px;
      border: 1px solid var(--line);
      background: var(--panel);
      box-shadow: var(--shadow);
      display: none;
    }
    .toast.show { display: block; }
    .toast.error { border-color: #f1a29a; color: var(--red); }
    .toast.ok { border-color: #9fd8bd; color: var(--green); }
    @media (max-width: 820px) {
      main { padding: 18px; }
      header { flex-direction: column; }
      .grid { grid-template-columns: 1fr; }
      .setup-form { grid-template-columns: 1fr; }
      .peer { grid-template-columns: 1fr; }
      .peer button { width: 100%; }
      .status-pill { white-space: normal; }
    }
  </style>
</head>
<body>
  <main>
    <header>
      <div>
        <h1>Codex Continuity Sync</h1>
        <div class="subhead">Same-account devices on this network appear here automatically.</div>
      </div>
      <div class="status-pill"><span class="dot"></span><span id="statusText">Starting</span></div>
    </header>
    <div class="setup" id="setupPanel">
      <h2>Connect This Account</h2>
      <div class="muted">Enter the same Codex account email or ID on each machine. It is hashed before storage or discovery.</div>
      <div class="setup-form">
        <input id="accountInput" type="text" autocomplete="email" placeholder="you@example.com">
        <button class="primary" id="saveAccountButton" type="button">Save Account</button>
      </div>
    </div>
    <div class="grid">
      <section>
        <div class="section-head">
          <div>
            <h2>Available Devices</h2>
            <div class="muted" id="peerCount">Looking for devices</div>
          </div>
          <button id="refreshButton" type="button">Refresh</button>
        </div>
        <div class="peer-list" id="peerList"></div>
      </section>
      <aside>
        <h2>This Device</h2>
        <div class="info">
          <div class="info-row">
            <div class="label">Name</div>
            <div class="value" id="deviceName">...</div>
          </div>
          <div class="info-row">
            <div class="label">Device ID</div>
            <div class="value" id="deviceId">...</div>
          </div>
          <div class="info-row">
            <div class="label">Account Check</div>
            <div class="value" id="accountMode">...</div>
          </div>
          <div class="info-row">
            <div class="label">Codex Home</div>
            <div class="value" id="codexHome">...</div>
          </div>
        </div>
      </aside>
    </div>
  </main>
  <div class="toast" id="toast"></div>
  <script>
    const peerList = document.getElementById("peerList");
    const peerCount = document.getElementById("peerCount");
    const statusText = document.getElementById("statusText");
    const toast = document.getElementById("toast");
    const setupPanel = document.getElementById("setupPanel");
    const accountInput = document.getElementById("accountInput");
    const buttons = new Map();
    const transfers = new Set();

    function showToast(message, kind = "ok") {
      toast.textContent = message;
      toast.className = `toast show ${kind}`;
      window.clearTimeout(showToast.timer);
      showToast.timer = window.setTimeout(() => {
        toast.className = "toast";
      }, 5200);
    }

    function relativeSeen(lastSeen) {
      const seconds = Math.max(0, Math.round(Date.now() / 1000 - lastSeen));
      if (seconds < 2) return "just now";
      if (seconds < 60) return `${seconds}s ago`;
      return `${Math.round(seconds / 60)}m ago`;
    }

    async function api(path, options) {
      const response = await fetch(path, options);
      if (!response.ok) {
        const text = await response.text();
        throw new Error(text || `HTTP ${response.status}`);
      }
      return response.json();
    }

    async function pull(deviceId, name) {
      if (transfers.has(deviceId)) return;
      transfers.add(deviceId);
      const button = buttons.get(deviceId);
      if (button) {
        button.disabled = true;
        button.textContent = "Transferring";
      }
      try {
        await api(`/pull?deviceId=${encodeURIComponent(deviceId)}`, { method: "POST" });
        showToast(`Transferred data from ${name}.`, "ok");
        await refresh();
      } catch (error) {
        showToast(`Transfer failed: ${error.message}`, "error");
      } finally {
        transfers.delete(deviceId);
        if (button) {
          button.disabled = false;
          button.textContent = "Transfer Data";
        }
      }
    }

    async function saveAccount() {
      const accountId = accountInput.value.trim();
      if (!accountId) {
        showToast("Enter the same account email or ID on each machine.", "error");
        return;
      }
      try {
        await api("/account", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ accountId })
        });
        accountInput.value = "";
        showToast("Account saved. Looking for matching devices.", "ok");
        await refresh();
      } catch (error) {
        showToast(`Could not save account: ${error.message}`, "error");
      }
    }

    function renderPeers(peers) {
      buttons.clear();
      peerList.innerHTML = "";
      peerCount.textContent = peers.length === 1 ? "1 device found" : `${peers.length} devices found`;
      if (!peers.length) {
        peerList.innerHTML = '<div class="empty">No same-account devices are visible yet.</div>';
        return;
      }
      for (const peer of peers) {
        const row = document.createElement("div");
        row.className = "peer";
        const info = document.createElement("div");
        info.innerHTML = `
          <div class="peer-name"></div>
          <div class="peer-meta">
            <span class="tag"></span>
            <span class="tag"></span>
            <span class="tag"></span>
          </div>
        `;
        info.querySelector(".peer-name").textContent = peer.deviceName || "Codex device";
        const tags = info.querySelectorAll(".tag");
        tags[0].textContent = peer.host || "unknown host";
        tags[1].textContent = `port ${peer.apiPort}`;
        tags[2].textContent = `seen ${relativeSeen(peer.lastSeen || 0)}`;
        const button = document.createElement("button");
        button.className = "primary";
        button.type = "button";
        button.textContent = "Transfer Data";
        button.disabled = transfers.has(peer.deviceId);
        if (button.disabled) button.textContent = "Transferring";
        button.addEventListener("click", () => pull(peer.deviceId, peer.deviceName || "device"));
        buttons.set(peer.deviceId, button);
        row.append(info, button);
        peerList.append(row);
      }
    }

    async function refresh() {
      try {
        const status = await api("/status");
        document.getElementById("deviceName").textContent = status.device.deviceName;
        document.getElementById("deviceId").textContent = status.device.deviceId;
        document.getElementById("accountMode").textContent = status.accountBinding.mode;
        document.getElementById("codexHome").textContent = status.codexHome;
        setupPanel.classList.toggle("show", status.accountBinding.mode === "unknown");
        statusText.textContent = "Running";
        const peers = await api("/peers");
        renderPeers(peers.peers || []);
      } catch (error) {
        statusText.textContent = "Needs attention";
        peerCount.textContent = "Could not load devices";
        showToast(error.message, "error");
      }
    }

    document.getElementById("refreshButton").addEventListener("click", refresh);
    document.getElementById("saveAccountButton").addEventListener("click", saveAccount);
    accountInput.addEventListener("keydown", (event) => {
      if (event.key === "Enter") saveAccount();
    });
    refresh();
    window.setInterval(refresh, 5000);
  </script>
</body>
</html>
"""


def now() -> float:
    return time.time()


def read_json(path: Path, default: object) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return default


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps(value, indent=2, sort_keys=True)
    tmp_path = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    tmp_path.write_text(body, encoding="utf-8")
    tmp_path.replace(path)


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

    def set_account_fingerprint(self, account_fingerprint: str | None) -> None:
        with self.lock:
            self.account_fingerprint = account_fingerprint
            self.peers = {}
            self._flush_locked()

    def _flush_locked(self) -> None:
        try:
            write_json(peer_store_path(self.codex_home), {"peers": list(self.peers.values())})
        except OSError as exc:
            print(f"Warning: could not write peer cache: {exc}")


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


def config_path(codex_home: Path) -> Path:
    return state_dir(codex_home) / "config.json"


def read_saved_account_id(codex_home: Path) -> str | None:
    value = read_json(config_path(codex_home), {})
    if isinstance(value, dict):
        account_id = value.get("accountId")
        if isinstance(account_id, str) and account_id.strip():
            return account_id.strip()
    return None


def save_account_id(codex_home: Path, account_id: str) -> None:
    write_json(config_path(codex_home), {"accountId": account_id.strip()})


def resolve_account_id(codex_home: Path, account_id: str | None) -> str | None:
    if account_id:
        return account_id
    env_account_id = os.environ.get("CODEX_ACCOUNT_ID")
    if env_account_id:
        return env_account_id
    return read_saved_account_id(codex_home)


def announce_loop(stop: threading.Event, payload_factory) -> None:
    with contextlib.closing(socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)) as sock:
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
        while not stop.is_set():
            with contextlib.suppress(OSError):
                sock.sendto(payload_factory(), (MULTICAST_GROUP, DISCOVERY_PORT))
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


def make_handler(codex_home: Path, peers: PeerStore, device: dict, app_state: dict):
    class Handler(BaseHTTPRequestHandler):
        server_version = "CodexContinuitySync/0.1"

        def do_GET(self) -> None:
            parsed = urllib.parse.urlparse(self.path)
            if parsed.path in {"/", "/app"}:
                self.write_html(APP_HTML)
            elif parsed.path == "/health":
                self.write_json({"ok": True, "plugin": PLUGIN_NAME})
            elif parsed.path == "/status":
                self.write_json(
                    {
                        "ok": True,
                        "plugin": PLUGIN_NAME,
                        "device": device,
                        "accountBinding": app_state["binding"],
                        "codexHome": str(codex_home),
                    }
                )
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
                    imported = pull_from_peer(peer, codex_home, app_state["account_id"])
                except RuntimeError as exc:
                    self.send_error(502, str(exc))
                    return
                self.write_json({"ok": True, "imported": imported, "peer": peer})
            elif parsed.path == "/account":
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    payload = json.loads(self.rfile.read(length).decode("utf-8"))
                    account_id = str(payload.get("accountId", "")).strip()
                except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
                    self.send_error(400, "Invalid account payload")
                    return
                if not account_id:
                    self.send_error(400, "Missing account ID")
                    return
                save_account_id(codex_home, account_id)
                app_state["account_id"] = account_id
                app_state["binding"] = continuity_pack.account_binding(codex_home, account_id)
                peers.set_account_fingerprint(app_state["binding"].get("fingerprint"))
                self.write_json({"ok": True, "accountBinding": app_state["binding"]})
            else:
                self.send_error(404)

        def write_pack(self) -> None:
            with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp:
                tmp_path = Path(tmp.name)
            try:
                args = argparse.Namespace(
                    source_codex_home=str(codex_home),
                    output=str(tmp_path),
                    account_id=app_state["account_id"],
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

        def write_html(self, value: str) -> None:
            body = value.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
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
    account_id = resolve_account_id(codex_home, args.account_id)
    binding = continuity_pack.account_binding(codex_home, account_id)
    app_state = {"account_id": account_id, "binding": binding}
    if not binding.get("fingerprint"):
        print("Account could not be inferred. Open the app and save your account email or ID.")
    peers = PeerStore(codex_home, device["deviceId"], binding.get("fingerprint"))
    server = ThreadingHTTPServer((args.host, args.port), make_handler(codex_home, peers, device, app_state))
    api_port = server.server_address[1]
    def payload_factory() -> bytes:
        return build_announcement(device, app_state["binding"], api_port)

    stop = threading.Event()
    threads = [
        threading.Thread(target=announce_loop, args=(stop, payload_factory), daemon=True),
        threading.Thread(target=listen_loop, args=(stop, peers), daemon=True),
    ]
    for thread in threads:
        thread.start()
    app_url = f"http://127.0.0.1:{api_port}/"
    print(f"{PLUGIN_NAME} app running for {device['deviceName']} at {app_url}")
    print("Peers with the same account and plugin will appear automatically.")
    if args.open:
        webbrowser.open(app_url)
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
    serve.add_argument("--open", action="store_true", help="Open the browser app after starting")
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
