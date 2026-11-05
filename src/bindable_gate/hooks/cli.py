# bindable-gate/src/bindable_gate/hooks/cli.py
"""``python -m bindable_gate hooks``: run the Agent Hook Gate, or print how to
install it.

    python -m bindable_gate hooks run --cli claude-code --config PATH
    python -m bindable_gate hooks run --cli codex --config PATH
    python -m bindable_gate hooks install --cli claude-code --base-url URL \\
        --agent-id DID --review-context test [--select-env NAME=VALUE]

``run`` is what the CLI's managed settings call for each hook event. ``install``
prints the configuration, the managed-settings entries and the exact commands
an administrator runs to install and to uninstall them. It changes nothing and
never runs ``sudo`` itself.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import sys
from pathlib import Path
from typing import Any, Final, TextIO

from bindable_gate.hooks import (
    claude_code,
    codex_cli,
    copilot_cli,
    cursor,
    gemini_cli,
    windsurf,
)
from bindable_gate.hooks.config import DEFAULT_CONFIG_DIR, ConfigError, HookConfig

#: The CLIs ``install`` prints the commands for. Codex is installed by
#: ``bindable-gate install codex``.
CLIS: Final[tuple[str, ...]] = (claude_code.CLI, copilot_cli.CLI)

#: The CLIs ``run`` handles events for.
RUN_CLIS: Final[tuple[str, ...]] = (*CLIS, codex_cli.CLI, cursor.CLI, gemini_cli.CLI, windsurf.CLI)

#: The adapters that take ``run --cli <name> --config PATH`` and nothing else.
_SIMPLE: Final[dict[str, Any]] = {
    codex_cli.CLI: codex_cli, cursor.CLI: cursor, gemini_cli.CLI: gemini_cli, windsurf.CLI: windsurf,
}

#: Claude Code's file-based managed settings on macOS, and the drop-in folder
#: whose files Claude Code merges after it (https://code.claude.com/docs/en/managed-settings).
CLAUDE_CODE_MANAGED_DIR: Final[str] = "/Library/Application Support/ClaudeCode"
CLAUDE_CODE_DROP_IN: Final[str] = f"{CLAUDE_CODE_MANAGED_DIR}/managed-settings.d/50-bindable-agent-hook.json"

#: Copilot CLI's machine-wide policy hooks on Linux and macOS, which load before
#: every other hook, cannot be turned off with disableAllHooks, and must be owned
#: by root (https://docs.github.com/en/copilot/reference/hooks-reference).
COPILOT_POLICY_DIR: Final[str] = "/etc/github-copilot/policy.d"
COPILOT_POLICY_FILE: Final[str] = f"{COPILOT_POLICY_DIR}/50-bindable-agent-hook.json"

DEFAULT_KEYCHAIN_SERVICE: Final[str] = "bindable-agent-hook"

_SELECTOR: Final[re.Pattern[str]] = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=([A-Za-z0-9_.\-]+)$")


def main(argv: list[str], *, stdout: TextIO | None = None, stderr: TextIO | None = None) -> int:
    out = stdout or sys.stdout
    err = stderr or sys.stderr
    parser = argparse.ArgumentParser(prog="python -m bindable_gate hooks")
    commands = parser.add_subparsers(dest="command", required=True)

    run = commands.add_parser("run", help="handle one hook event from standard input")
    run.add_argument("--cli", required=True, choices=RUN_CLIS)
    run.add_argument("--config", required=True, type=Path)
    run.add_argument("--event", choices=copilot_cli.EVENTS,
                     help="the event a Copilot CLI hook entry was configured for")

    install = commands.add_parser("install", help="print the install and uninstall commands")
    install.add_argument("--cli", required=True, choices=CLIS)
    install.add_argument("--base-url", required=True)
    install.add_argument("--agent-id", required=True, help="the agent's DID, did:webvh:<SCID>:<host>:agent:<Agent ID>")
    install.add_argument("--review-context", required=True)
    install.add_argument("--keychain-service", default=DEFAULT_KEYCHAIN_SERVICE)
    install.add_argument("--keychain-account", help="the agent's DID by default")
    install.add_argument("--config-path", type=Path, help=f"in {DEFAULT_CONFIG_DIR} by default")
    install.add_argument("--python", help="this Python by default")
    install.add_argument(
        "--select-env",
        help="govern only sessions started with this NAME=VALUE in their environment",
    )

    try:
        args = parser.parse_args(argv)
    except SystemExit as exit_:
        return int(exit_.code or 0)
    if args.command == "run":
        if args.cli == copilot_cli.CLI:
            return copilot_cli.main(args.config, args.event)
        if args.cli in _SIMPLE:
            return int(_SIMPLE[args.cli].main(args.config))
        return claude_code.main(args.config)
    return _install(args, out, err)


def _install(args: argparse.Namespace, out: TextIO, err: TextIO) -> int:
    config: dict[str, Any] = {
        "base_url": args.base_url,
        "agent_id": args.agent_id,
        "review_context": args.review_context,
        "keychain": {
            "service": args.keychain_service,
            "account": args.keychain_account or args.agent_id,
        },
    }
    try:
        HookConfig.from_mapping(config, source="The command line")
    except ConfigError as error:
        err.write(f"{error}\n")
        return 2
    selector: tuple[str, str] | None = None
    if args.select_env:
        match = _SELECTOR.match(args.select_env)
        if not match:
            err.write("--select-env takes NAME=VALUE, letters, digits, dot, dash and underscore only.\n")
            return 2
        selector = (match.group(1), match.group(2))
    config_path = str(args.config_path or Path(DEFAULT_CONFIG_DIR) / f"{args.cli}.json")
    python = args.python or sys.executable
    if args.cli == copilot_cli.CLI:
        out.write(copilot_instructions(
            python=python,
            config_path=config_path,
            config=config,
            policy=copilot_policy(python=python, config_path=config_path, selector=selector),
            selector=selector,
        ))
        return 0
    settings = managed_settings(python=python, config_path=config_path, selector=selector)
    out.write(instructions(
        python=python,
        config_path=config_path,
        config=config,
        settings=settings,
        selector=selector,
    ))
    return 0


def hook_command(
    *,
    python: str,
    config_path: str,
    blocking: bool,
    selector: tuple[str, str] | None,
    cli: str = claude_code.CLI,
) -> str:
    """The shell command one hook entry runs, for Claude Code or Codex.

    ``-I`` keeps the session's folder and environment off Python's path, so a
    package the session writes cannot stand in for this one. In ``PreToolUse``
    any exit other than 0 and 2, such as a Python that will not start, becomes
    exit 2 with one sentence, because Claude Code lets the tool through on any
    other code, and Codex documents no rule for it.
    """
    command = (
        f"{shlex.quote(python)} -I -m bindable_gate hooks run "
        f"--cli {cli} --config {shlex.quote(config_path)}"
    )
    if blocking:
        command += (
            '; s=$?; [ "$s" -eq 0 ] || [ "$s" -eq 2 ] || { echo '
            "'Action Gate blocked this tool because the agent hook could not run. "
            "Ask your administrator to check the hook installation.' >&2; exit 2; }; exit \"$s\""
        )
    else:
        command += "; exit 0"
    if selector is not None:
        name, value = selector
        command = f'[ "${{{name}:-}}" = {shlex.quote(value)} ] || exit 0; ' + command
    return command


def managed_settings(*, python: str, config_path: str, selector: tuple[str, str] | None) -> dict[str, Any]:
    """The hook entries for Claude Code's managed settings."""
    def entry(blocking: bool, matcher: str | None) -> list[dict[str, Any]]:
        hook = {
            "type": "command",
            "command": hook_command(python=python, config_path=config_path, blocking=blocking, selector=selector),
            "timeout": claude_code.HOOK_TIMEOUT_SECONDS,
        }
        group: dict[str, Any] = {"hooks": [hook]}
        if matcher is not None:
            group = {"matcher": matcher, **group}
        return [group]

    return {
        "hooks": {
            "PreToolUse": entry(True, "*"),
            "PostToolUse": entry(False, "*"),
            "PostToolUseFailure": entry(False, "*"),
            "PermissionDenied": entry(False, "*"),
            "Stop": entry(False, None),
            "SessionEnd": entry(False, None),
            "SessionStart": entry(False, None),
        }
    }


def codex_hooks(*, python: str, config_path: str, selector: tuple[str, str] | None) -> dict[str, Any]:
    """The hook entries for Codex's ``hooks.json``. They never rewrite a call:
    no entry prints ``updatedInput``."""
    def entry(blocking: bool, matcher: str | None) -> list[dict[str, Any]]:
        hook = {
            "type": "command",
            "command": hook_command(python=python, config_path=config_path, blocking=blocking,
                                    selector=selector, cli=codex_cli.CLI),
            "timeout": codex_cli.HOOK_TIMEOUT_SECONDS,
        }
        if blocking:
            hook["statusMessage"] = "Bindable Action Review"
        group: dict[str, Any] = {"hooks": [hook]}
        if matcher is not None:
            group = {"matcher": matcher, **group}
        return [group]

    return {
        "hooks": {
            "PreToolUse": entry(True, "*"),
            "PostToolUse": entry(False, "*"),
            "Stop": entry(False, None),
            "SessionStart": entry(False, None),
        }
    }


def cursor_hooks(*, python: str, config_path: str, selector: tuple[str, str] | None) -> dict[str, Any]:
    """The hook entries for Cursor's ``hooks.json``. ``failClosed`` makes Cursor
    block ``preToolUse`` on a crash, a timeout or no output."""
    def entry(event: str) -> list[dict[str, Any]]:
        blocking = event == "preToolUse"
        hook: dict[str, Any] = {
            "command": hook_command(python=python, config_path=config_path, blocking=blocking,
                                    selector=selector, cli=cursor.CLI),
            "timeout": cursor.HOOK_TIMEOUT_SECONDS,
        }
        if blocking:
            hook["failClosed"] = True
        return [hook]

    return {"version": 1, "hooks": {event: entry(event) for event in cursor.EVENTS}}


def gemini_hooks(*, python: str, config_path: str, selector: tuple[str, str] | None) -> dict[str, Any]:
    """The ``hooks`` for Gemini CLI's ``settings.json``. Its timeouts are in
    milliseconds."""
    def entry(event: str) -> list[dict[str, Any]]:
        hook = {
            "name": "bindable-gate",
            "type": "command",
            "command": hook_command(python=python, config_path=config_path, blocking=event == "BeforeTool",
                                    selector=selector, cli=gemini_cli.CLI),
            "timeout": gemini_cli.HOOK_TIMEOUT_SECONDS * 1000,
        }
        group: dict[str, Any] = {"hooks": [hook]}
        if event in ("BeforeTool", "AfterTool"):
            group = {"matcher": "*", **group}
        return [group]

    return {"hooks": {event: entry(event) for event in gemini_cli.EVENTS}}


def windsurf_hooks(*, python: str, config_path: str, selector: tuple[str, str] | None) -> dict[str, Any]:
    """The ``hooks`` for Devin's ``config.json``, which Windsurf's Devin Local reads."""
    def entry(event: str) -> list[dict[str, Any]]:
        hook = {
            "type": "command",
            "command": hook_command(python=python, config_path=config_path, blocking=event == "PreToolUse",
                                    selector=selector, cli=windsurf.CLI),
            "timeout": windsurf.HOOK_TIMEOUT_SECONDS,
        }
        group: dict[str, Any] = {"hooks": [hook]}
        if event in ("PreToolUse", "PostToolUse"):
            group = {"matcher": ".*", **group}
        return [group]

    return {"hooks": {event: entry(event) for event in windsurf.EVENTS}}


def instructions(
    *,
    python: str,
    config_path: str,
    config: dict[str, Any],
    settings: dict[str, Any],
    selector: tuple[str, str] | None,
) -> str:
    """The text ``install`` prints. Customer-facing."""
    q = shlex.quote
    config_dir = os.path.dirname(config_path)
    drop_in_dir = os.path.dirname(CLAUDE_CODE_DROP_IN)
    service = config["keychain"]["service"]
    account = config["keychain"]["account"]
    writable = _writable_by_you(python)
    lines = [
        "# Agent Hook Gate for Claude Code",
        "#",
        "# Run these commands yourself. This tool changes nothing.",
        "# The commands that start with sudo need an administrator's password.",
        "",
    ]
    if writable:
        lines += [
            f"# Warning: you can change {python} without an administrator's password.",
            "# A governed session could change it too. Install bindable-gate into a",
            "# Python that only an administrator can change, and run this again with it.",
            "",
        ]
    lines += [
        "# 1. The hook configuration. It holds no credential.",
        f"sudo mkdir -p {q(config_dir)}",
        f"sudo tee {q(config_path)} > /dev/null <<'JSON'",
        json.dumps(config, indent=2),
        "JSON",
        f"sudo chmod 644 {q(config_path)}",
        "",
        "# 2. The Gate credential, in the login keychain of the person who runs the",
        "#    agent. Run this as that person, not with sudo. It asks for the",
        "#    credential and does not show it.",
        f"security add-generic-password -U -s {q(service)} -a {q(account)} -w",
        "",
        "# 3. The hook entries, as a managed-settings drop-in. Claude Code merges it",
        "#    with any managed-settings.json already there, and a session cannot change it.",
        f"sudo mkdir -p {q(drop_in_dir)}",
        f"sudo tee {q(CLAUDE_CODE_DROP_IN)} > /dev/null <<'JSON'",
        json.dumps(settings, indent=2),
        "JSON",
        f"sudo chmod 644 {q(CLAUDE_CODE_DROP_IN)}",
        "",
        "# 4. Check. In a new Claude Code session, run /status: Setting sources names",
        "#    the managed settings. A file that is not valid JSON stops every Claude",
        "#    Code session from starting, so remove it at once if Claude Code says so.",
    ]
    if selector is not None:
        name, value = selector
        lines += [
            f"#    Only sessions started with {name}={value} are governed:",
            f"#    {name}={q(value)} claude",
        ]
    lines += [
        "",
        "# Uninstall",
        f"sudo rm -f {q(CLAUDE_CODE_DROP_IN)}",
        f"sudo rm -f {q(config_path)}",
        f"security delete-generic-password -s {q(service)} -a {q(account)}",
        "",
    ]
    return "\n".join(lines)


def _writable_by_you(python: str) -> bool:
    """Whether the current user can change this Python or the folder it is in."""
    path = Path(python)
    return any(os.access(p, os.W_OK) for p in (path, path.parent) if p.exists())


# ─── GitHub Copilot CLI ────────────────────────────────────────────────


def copilot_hook_command(*, python: str, config_path: str, event: str,
                         selector: tuple[str, str] | None) -> str:
    """The ``bash`` command one Copilot CLI policy hook runs.

    The camelCase input does not name its event, so ``--event`` does. Copilot
    CLI already denies a tool when ``preToolUse`` exits with any non-zero code;
    the wrapper adds the sentence the agent sees when the hook cannot run.
    """
    command = (
        f"{shlex.quote(python)} -I -m bindable_gate hooks run --cli {copilot_cli.CLI} "
        f"--event {event} --config {shlex.quote(config_path)}"
    )
    if event == "preToolUse":
        refusal = json.dumps({
            "permissionDecision": "deny",
            "permissionDecisionReason": "Action Gate blocked this tool because the agent hook "
            "could not run. Ask your administrator to check the hook installation.",
        })
        command += (
            f'; s=$?; [ "$s" -eq 0 ] || [ "$s" -eq 2 ] || {{ echo {shlex.quote(refusal)}; exit 2; }}; '
            'exit "$s"'
        )
    else:
        command += "; exit 0"
    if selector is not None:
        name, value = selector
        command = f'[ "${{{name}:-}}" = {shlex.quote(value)} ] || exit 0; ' + command
    return command


def copilot_policy(*, python: str, config_path: str, selector: tuple[str, str] | None) -> dict[str, Any]:
    """The Copilot CLI policy file: one command hook per event, every tool."""
    return {
        "version": 1,
        "hooks": {
            event: [{
                "type": "command",
                "bash": copilot_hook_command(python=python, config_path=config_path, event=event,
                                             selector=selector),
                "timeoutSec": copilot_cli.HOOK_TIMEOUT_SECONDS,
            }]
            for event in copilot_cli.EVENTS
        },
    }


def copilot_instructions(
    *,
    python: str,
    config_path: str,
    config: dict[str, Any],
    policy: dict[str, Any],
    selector: tuple[str, str] | None,
) -> str:
    """The text ``install --cli copilot-cli`` prints. Customer-facing."""
    q = shlex.quote
    service = config["keychain"]["service"]
    account = config["keychain"]["account"]
    target = q(COPILOT_POLICY_FILE)
    lines = [
        "# Agent Hook Gate for GitHub Copilot CLI",
        "#",
        "# Run these commands yourself. This tool changes nothing.",
        "# The commands that start with sudo need an administrator's password.",
        "",
    ]
    if _writable_by_you(python):
        lines += [
            f"# Warning: you can change {python} without an administrator's password.",
            "# A governed session could change it too. Install bindable-gate into a",
            "# Python that only an administrator can change, and run this again with it.",
            "",
        ]
    lines += [
        "# 1. The hook configuration. It holds no credential.",
        f"sudo mkdir -p {q(os.path.dirname(config_path))}",
        f"sudo tee {q(config_path)} > /dev/null <<'JSON'",
        json.dumps(config, indent=2),
        "JSON",
        f"sudo chmod 644 {q(config_path)}",
        "",
        "# 2. The Gate credential, in the login keychain of the person who runs the",
        "#    agent. Run this as that person, not with sudo. It asks for the",
        "#    credential and does not show it.",
        f"security add-generic-password -U -s {q(service)} -a {q(account)} -w",
        "",
        "# 3. The policy hooks. Copilot CLI runs them before every other hook, and",
        "#    disableAllHooks does not turn them off. The file must be owned by root",
        "#    and not writable by group or others. If a file with this name already",
        "#    exists, nothing is written: this command never replaces a policy file.",
        f"sudo mkdir -p {q(COPILOT_POLICY_DIR)}",
        (
            f"sudo test ! -e {target} && sudo tee {target} > /dev/null <<'JSON' "
            f"&& sudo chown root {target} && sudo chmod 644 {target} && echo installed"
        ),
        json.dumps(policy, indent=2),
        "JSON",
        "",
        "# 4. Check. In a new Copilot CLI session, run /env: it lists the hook files",
        "#    Copilot CLI loaded. The policy file must be among them.",
    ]
    if selector is not None:
        name, value = selector
        lines += [
            f"#    Only sessions started with {name}={value} are governed:",
            f"#    {name}={q(value)} copilot",
            "#    The hooks reference does not say that a hook sees the session's",
            "#    environment. Check with one session that the hook governs it.",
        ]
    lines += [
        "",
        "# Uninstall",
        f"sudo rm -f {target}",
        f"sudo rm -f {q(config_path)}",
        f"security delete-generic-password -s {q(service)} -a {q(account)}",
        "",
    ]
    return "\n".join(lines)
