# Codex Continuity Sync

Codex Continuity Sync is a prototype plugin for moving Codex continuity between machines that already have Codex installed but do not yet have the same chats, local data, memories, skills, or plugin state.

The first version is intentionally local-first:

- Export a signed manifest plus zip archive from one Codex home directory.
- Bind each pack to the same Codex account before import.
- Discover other machines on the same local network when they run the same plugin.
- Inspect a pack before trusting it.
- Import into another Codex home directory.
- Back up overwritten files during import.
- Exclude likely secrets by default.

## Current shape

The plugin includes:

- `skills/continuity-sync/SKILL.md` with Codex-facing usage instructions.
- `scripts/continuity_pack.py` for export, inspect, list, and import.
- `scripts/sync_agent.py` for the browser app, same-account LAN discovery, and peer-to-peer transfer.
- `start-continuity-sync.bat` as a Windows launcher that opens the browser app.
- `docs/auto-sync-design.md` with the no-command product direction.
- `hooks.json` and `.mcp.json` placeholders for future UI or MCP integration.

## App Mode

On Windows, double-click:

```text
start-continuity-sync.bat
```

That starts the local sync app and opens the browser dashboard. Same-account devices on the local network appear automatically. Click **Transfer Data** beside a device to pull its Codex continuity into this machine.

For the best result, close Codex on both machines before clicking **Transfer Data**, then reopen Codex on the destination machine after the transfer finishes. Codex Desktop keeps part of the visible chat list and state in SQLite files, so closing the app first makes the copied files cleaner and reopening makes Codex reload them.

For prototype testing with an explicit account ID:

```powershell
python .\plugins\codex-continuity-sync\scripts\sync_agent.py serve --account-id "your-codex-account-email-or-id" --open
```

## Friendly Auto-Sync Direction

The intended experience is that Codex starts `sync_agent.py` in the background when the plugin is installed. The agent announces this machine on the local network, finds other machines running the same plugin, filters them by the same account fingerprint, and exposes a tiny local API for a future UI to show devices and pull data with one click.

Until Codex plugin lifecycle hooks are wired in, the agent can be run manually for testing:

```powershell
python .\plugins\codex-continuity-sync\scripts\sync_agent.py serve --account-id "your-codex-account-email-or-id" --open
```

On another machine signed into the same account and running the same plugin, use the same command. Same-account peers will appear automatically. Different-account peers are ignored.

For machines on different networks, true no-setup discovery needs an account-scoped rendezvous service from Codex/OpenAI or from a trusted backend you control. The plugin should not guess public IPs or expose private data directly to the internet.

## Manual Pack Example

```powershell
python .\plugins\codex-continuity-sync\scripts\continuity_pack.py export --output .\codex-continuity.zip --account-id "your-codex-account-email-or-id"
python .\plugins\codex-continuity-sync\scripts\continuity_pack.py inspect .\codex-continuity.zip
python .\plugins\codex-continuity-sync\scripts\continuity_pack.py import .\codex-continuity.zip --target-codex-home $env:USERPROFILE\.codex --account-id "your-codex-account-email-or-id"
```

By default the exporter avoids files whose names look like tokens, credentials, keys, auth files, or sessions. Use `--include-secrets` only for a private, encrypted transport that you control.

## Same-account requirement

Continuity packs are meant for moving your own Codex data between your own machines, not between different Codex accounts.

For the strongest check, pass the same `--account-id` value during export and import. This value is hashed into the manifest, not stored directly. If `--account-id` is omitted, the script tries to infer a fingerprint from non-secret local profile/config files. If neither side can be verified, import is refused unless you explicitly pass `--allow-unknown-account`.

## What gets exported

The script looks for common Codex continuity locations under `CODEX_HOME` or `~/.codex`, including:

- `chats`, `sessions`, `conversations`, `threads`
- `memories`, `memory`, `state`, `data`
- `sqlite`
- `skills`, `plugins`
- common standalone files such as `.codex-global-state.json`, `session_index.jsonl`, `memory.json`, `memories.json`, and `settings.json`
- Codex Desktop SQLite state files such as `logs_*.sqlite` and `state_*.sqlite`, including their `-wal` and `-shm` sidecars

The exact Codex storage layout can change over time, so the script records every included file in `manifest.json`.
