# Automatic Same-Account Sync Design

The friendly target experience is:

1. The user installs the plugin on two machines.
2. Codex starts the plugin agent in the background.
3. Machines signed into the same Codex account discover each other.
4. Codex shows available devices and offers one-click import or sync.
5. Transfers refuse to run across different accounts.

## Local Discovery

The prototype uses UDP multicast on the local network:

- Multicast group: `239.77.77.77`
- Discovery port: `47777`
- Announcement interval: 5 seconds
- Peer expiry: 20 seconds

Each announcement includes:

- plugin name
- device ID
- device name
- account fingerprint
- local transfer API port

Machines only store peers when the account fingerprint matches.

## Cross-Network Discovery

For machines on different networks, the plugin needs an account-scoped rendezvous service. A plugin running only on two private machines cannot reliably discover the other machine through NAT, firewalls, and changing IP addresses without a shared service.

The intended production flow is:

1. Codex provides the plugin with the authenticated account ID.
2. Each machine registers an encrypted device presence record with an account-scoped rendezvous endpoint.
3. Devices fetch only records for the same account.
4. Transfers use direct local-network HTTP when possible.
5. If direct transfer is unavailable, the rendezvous service can broker a relay or exchange short-lived transfer tokens.

No chat or memory payload should be stored in rendezvous. It should store only device presence, capabilities, and short-lived connection metadata.

## Transfer API

Each agent exposes a tiny HTTP API:

- `GET /health`
- `GET /peers`
- `GET /pack`
- `POST /pull?deviceId=<id>`

`GET /pack` creates a fresh continuity pack with secrets excluded. `POST /pull` downloads a pack from the selected peer and imports it with backups and same-account verification.

## Account Binding

The best path is for Codex to provide the authenticated account ID to the plugin at runtime. Until then, the prototype supports:

- explicit `--account-id`
- inferred non-secret profile/config data

The account value is hashed before it is advertised or stored.

## Future Plugin Hook

When Codex plugin lifecycle hooks are available, the plugin should start:

```powershell
python .\scripts\sync_agent.py serve
```

The UI or MCP layer can call `GET /peers` and `POST /pull` so users do not need to run commands.
