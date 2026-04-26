#!/usr/bin/env python3
"""Create and restore portable Codex continuity packs.

The pack format is a zip file containing `manifest.json` plus the selected
files under a `payload/` directory. It is intentionally boring so it remains
easy to inspect and recover by hand.
"""

from __future__ import annotations

import argparse
import datetime as dt
import fnmatch
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import zipfile


DEFAULT_DIRS = (
    "chats",
    "sessions",
    "conversations",
    "threads",
    "memories",
    "memory",
    "state",
    "data",
    "skills",
    "plugins",
)

DEFAULT_FILES = (
    "memory.json",
    "memories.json",
    "settings.json",
    "preferences.json",
)

ACCOUNT_HINT_FILES = (
    "account.json",
    "profile.json",
    "user.json",
    "settings.json",
    "preferences.json",
    "config.json",
)

ACCOUNT_KEYS = (
    "account_id",
    "accountId",
    "user_id",
    "userId",
    "sub",
    "email",
    "username",
    "login",
)

SECRET_PATTERNS = (
    "*token*",
    "*secret*",
    "*credential*",
    "*auth*",
    "*apikey*",
    "*api_key*",
    "*session_key*",
    "*.pem",
    "*.key",
)


def codex_home_from_env() -> Path:
    value = os.environ.get("CODEX_HOME")
    if value:
        return Path(value).expanduser().resolve()
    return (Path.home() / ".codex").resolve()


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def stable_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def looks_secret(path: Path) -> bool:
    lowered_parts = [part.lower() for part in path.parts]
    lowered_name = path.name.lower()
    return any(fnmatch.fnmatch(lowered_name, pat) for pat in SECRET_PATTERNS) or any(
        part in {"auth", "credentials", "secrets", "tokens"} for part in lowered_parts
    )


def find_account_value(value: object) -> str | None:
    if isinstance(value, dict):
        for key in ACCOUNT_KEYS:
            found = value.get(key)
            if isinstance(found, str) and found.strip():
                return f"{key}:{found.strip().lower()}"
            if isinstance(found, (int, float)):
                return f"{key}:{found}"
        for child in value.values():
            found = find_account_value(child)
            if found:
                return found
    elif isinstance(value, list):
        for child in value:
            found = find_account_value(child)
            if found:
                return found
    return None


def account_binding(codex_home: Path, account_id: str | None) -> dict:
    if account_id:
        return {
            "mode": "explicit",
            "fingerprint": stable_hash(account_id.strip().lower()),
            "source": "--account-id",
        }

    for rel in ACCOUNT_HINT_FILES:
        path = codex_home / rel
        if not path.is_file() or looks_secret(Path(rel)):
            continue
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            continue
        found = find_account_value(value)
        if found:
            return {
                "mode": "inferred",
                "fingerprint": stable_hash(found),
                "source": rel,
            }

    return {
        "mode": "unknown",
        "fingerprint": None,
        "source": None,
    }


def iter_candidates(source: Path, include_secrets: bool) -> list[Path]:
    candidates: list[Path] = []

    for name in DEFAULT_DIRS:
        root = source / name
        if root.exists():
            candidates.extend(path for path in root.rglob("*") if path.is_file())

    for name in DEFAULT_FILES:
        path = source / name
        if path.is_file():
            candidates.append(path)

    unique = sorted(set(candidates))
    if include_secrets:
        return unique
    return [path for path in unique if not looks_secret(path.relative_to(source))]


def write_export(args: argparse.Namespace) -> int:
    source = Path(args.source_codex_home).expanduser().resolve()
    output = Path(args.output).expanduser().resolve()

    if not source.exists():
        print(f"Codex home does not exist: {source}", file=sys.stderr)
        return 2

    files = iter_candidates(source, include_secrets=args.include_secrets)
    output.parent.mkdir(parents=True, exist_ok=True)

    manifest = {
        "format": "codex-continuity-pack-v1",
        "createdAt": utc_now(),
        "sourceCodexHome": str(source),
        "accountBinding": account_binding(source, args.account_id),
        "includeSecrets": bool(args.include_secrets),
        "fileCount": len(files),
        "files": [],
    }

    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in files:
            rel = path.relative_to(source).as_posix()
            manifest["files"].append(
                {
                    "path": rel,
                    "size": path.stat().st_size,
                    "sha256": sha256(path),
                }
            )
            archive.write(path, f"payload/{rel}")

        archive.writestr("manifest.json", json.dumps(manifest, indent=2, sort_keys=True))

    print(f"Wrote {output}")
    print(f"Included {len(files)} files from {source}")
    if not args.include_secrets:
        print("Secret-looking files were excluded.")
    binding = manifest["accountBinding"]
    if binding["mode"] == "unknown":
        print("Account binding could not be inferred. Re-export with --account-id to enforce same-account imports.")
    else:
        print(f"Account binding recorded using {binding['mode']} source: {binding['source']}")
    return 0


def load_manifest(pack: Path) -> dict:
    with zipfile.ZipFile(pack, "r") as archive:
        with archive.open("manifest.json") as handle:
            return json.load(handle)


def inspect_pack(args: argparse.Namespace) -> int:
    pack = Path(args.pack).expanduser().resolve()
    manifest = load_manifest(pack)
    print(json.dumps(manifest, indent=2, sort_keys=True))
    return 0


def list_pack(args: argparse.Namespace) -> int:
    pack = Path(args.pack).expanduser().resolve()
    manifest = load_manifest(pack)
    for item in manifest.get("files", []):
        print(item["path"])
    return 0


def is_safe_member(name: str) -> bool:
    return name.startswith("payload/") and ".." not in Path(name).parts


def safe_relative_path(value: str) -> Path:
    rel = Path(value)
    if rel.is_absolute() or ".." in rel.parts:
        raise ValueError(f"Unsafe manifest path: {value}")
    return rel


def backup_existing(path: Path, backup_root: Path, target: Path) -> None:
    if not path.exists():
        return
    rel = path.relative_to(target)
    backup_path = backup_root / rel
    backup_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path, backup_path)


def verify_account_binding(manifest: dict, target: Path, account_id: str | None, allow_unknown: bool) -> int:
    source_binding = manifest.get("accountBinding") or {}
    source_fingerprint = source_binding.get("fingerprint")
    if not source_fingerprint:
        if allow_unknown:
            print("Pack has no account binding; continuing because --allow-unknown-account was set.")
            return 0
        print("Pack has no account binding. Re-export with --account-id, or use --allow-unknown-account.", file=sys.stderr)
        return 7

    target_binding = account_binding(target, account_id)
    target_fingerprint = target_binding.get("fingerprint")
    if not target_fingerprint:
        if allow_unknown:
            print("Destination account could not be inferred; continuing because --allow-unknown-account was set.")
            return 0
        print("Destination account could not be verified. Pass the same --account-id used for export.", file=sys.stderr)
        return 8

    if source_fingerprint != target_fingerprint:
        print("Account mismatch: this continuity pack belongs to a different Codex account.", file=sys.stderr)
        print("Import refused. Use the same account on both machines.", file=sys.stderr)
        return 9

    print(f"Account binding verified using {target_binding['mode']} source: {target_binding['source']}")
    return 0


def write_import(args: argparse.Namespace) -> int:
    pack = Path(args.pack).expanduser().resolve()
    target = Path(args.target_codex_home).expanduser().resolve()
    target.mkdir(parents=True, exist_ok=True)

    manifest = load_manifest(pack)
    account_status = verify_account_binding(
        manifest,
        target,
        account_id=args.account_id,
        allow_unknown=args.allow_unknown_account,
    )
    if account_status:
        return account_status

    backup_root = target / "continuity-backups" / utc_now().replace(":", "-")

    with zipfile.ZipFile(pack, "r") as archive:
        names = archive.namelist()
        unsafe = [name for name in names if name.startswith("payload/") and not is_safe_member(name)]
        if unsafe:
            print(f"Pack contains unsafe paths: {unsafe}", file=sys.stderr)
            return 3

        for item in manifest.get("files", []):
            try:
                rel = safe_relative_path(item["path"])
            except ValueError as exc:
                print(str(exc), file=sys.stderr)
                return 5
            dest = target / rel
            member = f"payload/{rel.as_posix()}"
            if member not in names:
                print(f"Manifest entry missing from archive: {item['path']}", file=sys.stderr)
                return 4
            if args.backup:
                backup_existing(dest, backup_root, target)
            dest.parent.mkdir(parents=True, exist_ok=True)
            digest = hashlib.sha256()
            with archive.open(member) as src, dest.open("wb") as out:
                for chunk in iter(lambda: src.read(1024 * 1024), b""):
                    digest.update(chunk)
                    out.write(chunk)
            expected = item.get("sha256")
            if expected and digest.hexdigest() != expected:
                print(f"Checksum mismatch after import: {item['path']}", file=sys.stderr)
                return 6

    print(f"Imported {manifest.get('fileCount', 0)} files into {target}")
    if args.backup:
        print(f"Backups, when needed, were written under {backup_root}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    export = sub.add_parser("export", help="Create a continuity pack")
    export.add_argument("--source-codex-home", default=str(codex_home_from_env()))
    export.add_argument("--output", required=True)
    export.add_argument("--account-id", help="Stable account identifier required again on import when Codex cannot infer one")
    export.add_argument("--include-secrets", action="store_true")
    export.set_defaults(func=write_export)

    inspect = sub.add_parser("inspect", help="Print manifest.json")
    inspect.add_argument("pack")
    inspect.set_defaults(func=inspect_pack)

    list_cmd = sub.add_parser("list", help="List files in a pack")
    list_cmd.add_argument("pack")
    list_cmd.set_defaults(func=list_pack)

    import_cmd = sub.add_parser("import", help="Import a continuity pack")
    import_cmd.add_argument("pack")
    import_cmd.add_argument("--target-codex-home", default=str(codex_home_from_env()))
    import_cmd.add_argument("--account-id", help="Stable account identifier used to verify this is the same account")
    import_cmd.add_argument("--allow-unknown-account", action="store_true", help="Bypass only when account identity cannot be inferred")
    import_cmd.add_argument("--no-backup", dest="backup", action="store_false")
    import_cmd.set_defaults(func=write_import, backup=True)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
