---
name: continuity-sync
description: Export, inspect, and import Codex continuity packs so chats, memories, skills, plugins, and local context can move between machines without copying secrets by default.
---

# Codex Continuity Sync

Use this skill when the user wants to transfer Codex chats, remembered context, local state, skills, or plugin data to another computer.

## Workflow

1. Prefer the automatic same-account agent for user-friendly transfers.
2. Use `scripts/sync_agent.py serve` as the background service that discovers matching machines.
3. Only show or sync peers whose account fingerprint matches this machine.
4. Fall back to manual continuity packs when background discovery is unavailable.
5. Remind the user that secrets are excluded unless they explicitly use `--include-secrets`.

## Commands

For the automatic local-network agent:

```powershell
python .\scripts\sync_agent.py serve --account-id "your-codex-account-email-or-id"
python .\scripts\sync_agent.py peers
```

For manual pack transfer from the plugin root:

```powershell
python .\scripts\continuity_pack.py export --output .\codex-continuity.zip --account-id "your-codex-account-email-or-id"
python .\scripts\continuity_pack.py inspect .\codex-continuity.zip
python .\scripts\continuity_pack.py import .\codex-continuity.zip --target-codex-home $env:USERPROFILE\.codex --account-id "your-codex-account-email-or-id"
```

## Safety rules

- Enforce same-account transfer. A pack exported for one Codex account must not be imported into another account.
- Prefer explicit `--account-id` on both export and import because Codex account metadata may not be stored consistently across machines.
- Ignore discovered devices when the account fingerprint does not match.
- Do not export auth tokens, API keys, credentials, session secrets, or keychains unless the user explicitly asks and confirms they control the transfer medium.
- Do not overwrite destination data without creating backups.
- Prefer inspecting `manifest.json` before import if the pack came from another machine or person.
- Treat the pack as sensitive because chats and memories can contain private information even when secrets are excluded.
