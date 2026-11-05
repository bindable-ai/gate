# bindable-gate/src/bindable_gate/cli.py
"""The ``bindable-gate`` command.

    bindable-gate                          run the local MCP proxy (configured by BINDABLE_*)
    bindable-gate install claude-code      write the Claude Code hook, in one command
    bindable-gate uninstall claude-code    remove it
    bindable-gate install codex            write the Codex CLI hook, in one command
    bindable-gate uninstall codex          remove it
    bindable-gate install copilot-cli      write the GitHub Copilot CLI hook, in one command
    bindable-gate uninstall copilot-cli    remove it
    bindable-gate install cursor           write the Cursor hook, in one command
    bindable-gate install gemini-cli       write the Gemini CLI hook, in one command
    bindable-gate install windsurf         write the Windsurf (Devin Local) hook, in one command
    bindable-gate uninstall <runtime>      remove it
    bindable-gate track verify [PATH]      recompute a local Track's hash chain
    bindable-gate offline accept           continue unreviewed while Bindable is unreachable
    bindable-gate offline status           each agent's offline state on this machine
    bindable-gate offline verify           recompute an agent's offline records' hash chain
    bindable-gate hooks ...                the hook's own commands (run, and the printed install)

``python -m bindable_gate`` is the same command.
"""

from __future__ import annotations

import argparse
import re
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Final, TextIO

from bindable_gate.library.outcome import REVIEW_CONTEXTS

RUNTIMES: Final[tuple[str, ...]] = ("claude-code", "codex", "copilot-cli", "cursor", "gemini-cli", "windsurf")

_SELECTOR: Final[re.Pattern[str]] = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=([A-Za-z0-9_.\-]+)$")


def main(argv: list[str] | None = None, *, stdout: TextIO | None = None, stderr: TextIO | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    out = stdout or sys.stdout
    err = stderr or sys.stderr
    if not args or args[0] not in ("install", "uninstall", "track", "offline", "hooks", "-h", "--help"):
        from bindable_gate.mcp.proxy import main as proxy_main

        proxy_main()
        return 0
    if args[0] == "hooks":
        from bindable_gate.hooks.cli import main as hooks_main

        return hooks_main(args[1:], stdout=out, stderr=err)
    return _commands(args, out, err)


def _parser() -> argparse.ArgumentParser:
    from bindable_gate.hooks.install import DEFAULT_KEYCHAIN_SERVICE, SCOPES

    parser = argparse.ArgumentParser(
        prog="bindable-gate",
        description="With no command, runs the local MCP proxy configured by BINDABLE_* variables.",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    install = commands.add_parser("install", help="write the hook for an agent runtime")
    install.add_argument("runtime", choices=RUNTIMES)
    install.add_argument("--agent-id", help="the agent's DID, did:webvh:<SCID>:<host>:agent:<Agent ID>; leave out to record locally")
    install.add_argument("--base-url", help="the Bindable API base URL; leave out to record locally")
    install.add_argument("--review-context", default="test", choices=REVIEW_CONTEXTS)
    install.add_argument("--agent-key-file", help="a PEM ECDSA P-256 private key that signs every Proposal")
    install.add_argument("--agent-key-id", help="the key's id, <did:webvh DID>#<key uuid>; required with --agent-key-file")
    install.add_argument("--keychain-service", default=DEFAULT_KEYCHAIN_SERVICE)
    install.add_argument("--keychain-account", help="the agent's DID by default")
    install.add_argument("--scope", choices=SCOPES, default="user")
    install.add_argument("--config", type=Path, help="where the hook configuration goes")
    install.add_argument("--settings", type=Path,
                         help="the Claude Code settings file, or Codex's hooks.json, to write")
    install.add_argument("--python", help="the Python the hook runs; this one by default")
    install.add_argument("--select-env", help="govern only sessions started with NAME=VALUE")

    uninstall = commands.add_parser("uninstall", help="remove the hook")
    uninstall.add_argument("runtime", choices=RUNTIMES)
    uninstall.add_argument("--scope", choices=SCOPES, default="user")
    uninstall.add_argument("--config", type=Path)
    uninstall.add_argument("--settings", type=Path)

    track = commands.add_parser("track", help="the local Track")
    track_commands = track.add_subparsers(dest="track_command", required=True)
    verify = track_commands.add_parser("verify", help="recompute the hash chain")
    verify.add_argument("path", nargs="?", type=Path)
    verify.add_argument("--public-key", type=Path, help="a PEM public key every line must be signed by")

    offline = commands.add_parser("offline", help="offline mode, while Bindable is unreachable")
    offline_commands = offline.add_subparsers(dest="offline_command", required=True)
    accept = offline_commands.add_parser(
        "accept", help="let this agent continue unreviewed on this machine until Bindable is back")
    accept.add_argument("--agent-id", help="the agent's DID; needed only when several agents are waiting")
    offline_commands.add_parser("status", help="each agent's offline state on this machine")
    records = offline_commands.add_parser("verify", help="recompute an agent's offline records' hash chain")
    records.add_argument("--agent-id", help="the agent's DID; needed only when several agents have records")
    records.add_argument("--public-key", type=Path, help="a PEM public key every record must be signed by")
    return parser


def _commands(argv: list[str], out: TextIO, err: TextIO) -> int:
    parser = _parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exit_:
        return int(exit_.code or 0)
    if args.command == "track":
        return _verify(args.path, args.public_key, out, err)
    if args.command == "offline":
        return _offline(args, out, err)

    from bindable_gate.hooks.install import (
        InstallError,
        hook_configuration,
        install,
        uninstall,
    )

    try:
        if args.command == "uninstall":
            uninstall(scope=args.scope, config_path=args.config, settings_path=args.settings, out=out,
                      runtime=args.runtime)
            return 0
        selector: tuple[str, str] | None = None
        if args.select_env:
            match = _SELECTOR.match(args.select_env)
            if not match:
                raise InstallError("--select-env takes NAME=VALUE: letters, digits, dot, dash and underscore only.")
            selector = (match.group(1), match.group(2))
        config = hook_configuration(
            agent_id=args.agent_id,
            base_url=args.base_url,
            review_context=args.review_context,
            agent_key_file=args.agent_key_file,
            agent_key_id=args.agent_key_id,
            keychain_service=args.keychain_service,
            keychain_account=args.keychain_account,
        )
        install(
            scope=args.scope,
            config=config,
            python=args.python or sys.executable,
            config_path=args.config,
            settings_path=args.settings,
            selector=selector,
            out=out,
            runtime=args.runtime,
        )
    except (InstallError, OSError) as error:
        message = str(error) if isinstance(error, InstallError) else (
            f"{error.filename or 'A file'} could not be written ({error.strerror}).")
        err.write(message + "\n")
        return 2
    return 0


def _verify(path: Path | None, public_key: Path | None, out: TextIO, err: TextIO) -> int:
    from bindable_gate.contract.local_track import (
        default_local_track_path,
        verify_local_track,
    )
    from bindable_gate.contract.signing import load_public_key

    target = path or default_local_track_path()
    try:
        key = load_public_key(public_key.read_bytes()) if public_key is not None else None
    except (OSError, ValueError):
        err.write(f"{public_key} is not a PEM ECDSA P-256 public key.\n")
        return 2
    try:
        check = verify_local_track(target, key)
    except OSError:
        err.write(f"{target} could not be read.\n")
        return 2
    if check.ok:
        signed = ", every line signed by the key given" if key is not None else ""
        lines = f"{check.lines} line" + ("" if check.lines == 1 else "s")
        out.write(f"{target}: the chain holds, {lines}{signed}.\n")
        return 0
    out.write(f"{target}: broken at line {check.broken_at} of {check.lines}: {check.reason}.\n")
    return 1


def _offline(args: argparse.Namespace, out: TextIO, err: TextIO) -> int:
    from bindable_gate.contract import offline

    if args.offline_command == "accept":
        try:
            accepted = offline.accept(args.agent_id)
        except offline.OfflineError as error:
            err.write(f"{error}\n")
            return 1
        out.write(
            f"Accepted for agent {accepted.agent_id} on this machine by {accepted.accepted_by} at "
            f"{accepted.accepted_at}. Its actions run, not reviewed, and are kept as offline records "
            "until Bindable is reachable again.\n"
        )
        return 0
    found = offline.agents()
    now = datetime.now(UTC)
    if args.offline_command == "status":
        if not found:
            out.write("No agent on this machine has offline records or an outage.\n")
        for agent in found:
            state = "unreachable" if agent.offline else "reachable"
            line = f"{agent.agent_id}: Bindable {state}"
            if agent.offline:
                line += f" since {agent.offline_since}, setting {offline.SETTING_NAMES[agent.setting]}"
                line += (f", accepted by {agent.accepted_by} at {agent.accepted_at}" if agent.accepted_at
                         else ", stopped: nobody accepted within 10 minutes" if agent.stopped(now)
                         else ", waiting for bindable-gate offline accept" if agent.waiting else "")
            out.write(f"{line}; {offline.counted(agent.records)} ({offline.NOT_REVIEWED.lower()}), "
                      f"{agent.pending} not yet sent.\n")
        return 0
    chosen = [a for a in found if args.agent_id in (None, a.agent_id) and a.records]
    if len(chosen) != 1:
        err.write("Name one agent with --agent-id: " + (", ".join(a.agent_id for a in chosen)
                  or "no agent has offline records") + ".\n")
        return 2
    from bindable_gate.contract.signing import load_public_key

    try:
        key = load_public_key(args.public_key.read_bytes()) if args.public_key is not None else None
    except (OSError, ValueError):
        err.write(f"{args.public_key} is not a PEM ECDSA P-256 public key.\n")
        return 2
    broken = False
    for name in chosen[0].chains:
        path = offline.agent_folder(chosen[0].agent_id) / name
        check = offline.verify_records(path, key)
        if check.ok:
            signed = ", every record signed by the key given" if key is not None else ""
            out.write(f"{path}: the chain holds, {offline.counted(check.records)}{signed}.\n")
        else:
            broken = True
            out.write(f"{path}: broken at record {check.broken_at} of {check.records}: {check.reason}.\n")
    return 1 if broken else 0


if __name__ == "__main__":
    sys.exit(main())
