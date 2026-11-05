# bindable-gate/src/bindable_gate/hooks/install.py
"""``bindable-gate install claude-code`` and ``bindable-gate install codex``:
write the hook in one command.

It writes two files and nothing else:

1. The hook configuration, which holds no credential.
2. The hook entries:

   - Claude Code, ``--scope user`` (the default): merged into
     ``~/.claude/settings.json``. Every other setting and every other hook
     there is kept, and a copy of the file as it was is left beside it. A
     session can edit this file, so this scope is for a person governing their
     own agent.
   - Claude Code, ``--scope managed``: the managed-settings drop-in, which a
     session cannot change. Run it with ``sudo``.
   - GitHub Copilot CLI, ``--scope user``: its own file,
     ``bindable-gate.json``, in ``$COPILOT_HOME/hooks`` (``~/.copilot/hooks``
     by default). ``--scope managed``: the machine-wide policy file in
     ``/etc/github-copilot/policy.d``, which a session cannot change and
     ``disableAllHooks`` does not turn off. Run it with ``sudo``.
   - Codex: merged into ``hooks.json`` in ``$CODEX_HOME`` (``~/.codex`` by
     default), the same way.
   - Cursor: merged into ``~/.cursor/hooks.json``, or with ``--scope managed``
     into the enterprise ``hooks.json`` (``/Library/Application
     Support/Cursor`` on macOS, ``/etc/cursor`` on Linux).
   - Gemini CLI: merged into the ``hooks`` of ``~/.gemini/settings.json``, or
     with ``--scope managed`` of the system ``/etc/gemini-cli/settings.json``.
   - Windsurf (Devin Local): merged into the ``hooks`` of
     ``~/.config/devin/config.json``. Devin documents no managed hooks, so
     ``--scope managed`` is refused. Codex runs a new or changed hook only after the
     person trusts it with ``/hooks``. Managed Codex hooks are set by an
     administrator in ``requirements.toml``, so ``--scope managed`` is refused.

With ``--agent-id`` and ``--base-url`` the hook is connected and reads the Gate
credential from the macOS keychain; the command prints the one ``security``
line that stores it, which asks for the credential and does not show it. With
neither, the hook is standalone: every call is allowed and recorded on the
local Track.

``bindable-gate uninstall claude-code`` removes exactly what this wrote.
"""

from __future__ import annotations

import json
import os
import shlex
import sys
import tempfile
from collections.abc import Mapping
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
from bindable_gate.hooks.cli import (
    CLAUDE_CODE_DROP_IN,
    COPILOT_POLICY_FILE,
    DEFAULT_KEYCHAIN_SERVICE,
    codex_hooks,
    copilot_policy,
    cursor_hooks,
    gemini_hooks,
    managed_settings,
    windsurf_hooks,
)
from bindable_gate.hooks.config import DEFAULT_CONFIG_DIR, ConfigError, HookConfig

SCOPES: Final[tuple[str, ...]] = ("user", "managed")

#: The runtimes this command writes the hook for.
RUNTIMES: Final[tuple[str, ...]] = (
    claude_code.CLI, codex_cli.CLI, copilot_cli.CLI, cursor.CLI, gemini_cli.CLI, windsurf.CLI,
)

#: The hook entries each runtime's file takes, by runtime.
_ENTRIES: Final[Mapping[str, Any]] = {
    claude_code.CLI: managed_settings,
    codex_cli.CLI: codex_hooks,
    copilot_cli.CLI: copilot_policy,
    cursor.CLI: cursor_hooks,
    gemini_cli.CLI: gemini_hooks,
    windsurf.CLI: windsurf_hooks,
}

CURSOR_MANAGED: Final[str] = (
    "/Library/Application Support/Cursor/hooks.json" if sys.platform == "darwin" else "/etc/cursor/hooks.json"
)
GEMINI_MANAGED: Final[str] = "/etc/gemini-cli/settings.json"

#: The file ``install copilot-cli`` writes in the user's Copilot CLI hooks folder.
COPILOT_USER_FILE: Final[str] = "bindable-gate.json"

#: What marks a hook entry as this Gate's, so a reinstall replaces it and an
#: uninstall removes it and nothing else.
MARK: Final[str] = f"bindable_gate hooks run --cli {claude_code.CLI}"

CONFIG_NAME: Final[str] = f"{claude_code.CLI}.json"
BACKUP_SUFFIX: Final[str] = ".before-bindable-gate"

_NAMES: Final[Mapping[str, str]] = {
    claude_code.CLI: "Claude Code", codex_cli.CLI: "Codex", copilot_cli.CLI: "GitHub Copilot CLI",
    cursor.CLI: "Cursor", gemini_cli.CLI: "Gemini CLI", windsurf.CLI: "Windsurf (Devin Local)",
}


def _whole_file(scope: str, runtime: str) -> bool:
    """Whether the hook file is this Gate's alone, written whole and removed
    whole, rather than merged into a file with other settings."""
    return runtime == copilot_cli.CLI or (runtime == claude_code.CLI and scope == "managed")


def mark_for(runtime: str) -> str:
    """What marks a hook entry as this Gate's for ``runtime``."""
    return f"bindable_gate hooks run --cli {runtime} "


class InstallError(Exception):
    """The install cannot go ahead. Nothing was changed. One sentence."""


def user_settings_path() -> Path:
    return Path.home() / ".claude" / "settings.json"


def copilot_hooks_path() -> Path:
    home = os.environ.get("COPILOT_HOME")
    return (Path(home) if home else Path.home() / ".copilot") / "hooks" / COPILOT_USER_FILE


def codex_hooks_path() -> Path:
    home = os.environ.get("CODEX_HOME")
    return (Path(home) if home else Path.home() / ".codex") / "hooks.json"


def user_config_path(runtime: str = claude_code.CLI) -> Path:
    if sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support" / "Bindable" / "agent-hook"
    else:
        config_home = os.environ.get("XDG_CONFIG_HOME")
        base = (Path(config_home) if config_home else Path.home() / ".config") / "bindable" / "agent-hook"
    return base / f"{runtime}.json"


def default_paths(scope: str, runtime: str = claude_code.CLI) -> tuple[Path, Path]:
    """The configuration and the settings file for ``scope``."""
    if runtime == codex_cli.CLI:
        if scope == "managed":
            raise InstallError(
                "Codex's managed hooks are set by an administrator in requirements.toml, "
                "so this command writes the user scope only. Nothing was changed."
            )
        return user_config_path(runtime), codex_hooks_path()
    if runtime == copilot_cli.CLI:
        if scope == "managed":
            return Path(DEFAULT_CONFIG_DIR) / f"{runtime}.json", Path(COPILOT_POLICY_FILE)
        return user_config_path(runtime), copilot_hooks_path()
    if runtime == cursor.CLI:
        if scope == "managed":
            return Path(DEFAULT_CONFIG_DIR) / f"{runtime}.json", Path(CURSOR_MANAGED)
        return user_config_path(runtime), Path.home() / ".cursor" / "hooks.json"
    if runtime == gemini_cli.CLI:
        if scope == "managed":
            return Path(DEFAULT_CONFIG_DIR) / f"{runtime}.json", Path(GEMINI_MANAGED)
        return user_config_path(runtime), Path.home() / ".gemini" / "settings.json"
    if runtime == windsurf.CLI:
        if scope == "managed":
            raise InstallError(
                "Devin, which runs Windsurf's agent, documents no managed hooks, so this "
                "command writes the user scope only. Nothing was changed."
            )
        return user_config_path(runtime), Path.home() / ".config" / "devin" / "config.json"
    if scope == "managed":
        return Path(DEFAULT_CONFIG_DIR) / CONFIG_NAME, Path(CLAUDE_CODE_DROP_IN)
    return user_config_path(runtime), user_settings_path()


def hook_configuration(
    *,
    agent_id: str | None,
    base_url: str | None,
    review_context: str,
    agent_key_file: str | None,
    agent_key_id: str | None,
    keychain_service: str,
    keychain_account: str | None,
) -> dict[str, Any]:
    """The hook configuration, validated. Connected with an agent and a base
    URL; standalone with neither."""
    if (agent_id is None) != (base_url is None):
        raise InstallError(
            "Give both --agent-id and --base-url to connect, or neither to record locally."
        )
    config: dict[str, Any] = {"review_context": review_context}
    if agent_id is not None and base_url is not None:
        config.update(base_url=base_url, agent_id=agent_id)
    if agent_key_file is not None:
        config["agent_key_file"] = str(Path(agent_key_file).expanduser().resolve())
    if agent_key_id is not None:
        config["agent_key_id"] = agent_key_id
    try:
        checked = HookConfig.from_mapping(config, source="The command line")
    except ConfigError as error:
        raise InstallError(str(error)) from None
    if agent_id is not None:
        config["keychain"] = {
            "service": keychain_service,
            "account": keychain_account or checked.did,
        }
    try:
        checked = HookConfig.from_mapping(config, source="The command line")
        checked.agent_key()
    except ConfigError as error:
        raise InstallError(str(error)) from None
    return config


def merged(settings: Mapping[str, Any], entries: Mapping[str, Any], runtime: str = claude_code.CLI) -> dict[str, Any]:
    """``settings`` with this Gate's hook entries in place of any earlier ones,
    and every other setting and hook kept as it was."""
    result = dict(settings)
    hooks = dict(result.get("hooks") or {})
    for event, groups in hooks.items():
        if isinstance(groups, list):
            hooks[event] = [group for group in groups if not _ours(group, runtime)]
    for event, groups in entries["hooks"].items():
        hooks[event] = [*(hooks.get(event) or []), *groups]
    result["hooks"] = {event: groups for event, groups in hooks.items() if groups}
    for key, value in entries.items():
        if key != "hooks":
            result.setdefault(key, value)
    return result


def without_ours(settings: Mapping[str, Any], runtime: str = claude_code.CLI) -> dict[str, Any]:
    """``settings`` with this Gate's hook entries removed and nothing else."""
    result = dict(settings)
    hooks = result.get("hooks")
    if not isinstance(hooks, dict):
        return result
    kept = {}
    for event, groups in hooks.items():
        if isinstance(groups, list):
            groups = [group for group in groups if not _ours(group, runtime)]
            if groups:
                kept[event] = groups
        else:
            kept[event] = groups
    if kept:
        result["hooks"] = kept
    else:
        result.pop("hooks", None)
    return result


def _ours(group: Any, runtime: str = claude_code.CLI) -> bool:
    if not isinstance(group, dict):
        return False
    mark = mark_for(runtime)
    if mark in str(group.get("command") or ""):  # Cursor's entries are flat
        return True
    return any(
        isinstance(hook, dict) and mark in str(hook.get("command") or "")
        for hook in group.get("hooks") or []
    )


def _read_settings(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8") or "{}")
    except (OSError, ValueError):
        raise InstallError(f"{path} is not valid JSON, so nothing was changed. Fix it first.") from None
    if not isinstance(value, dict):
        raise InstallError(f"{path} is not a JSON object, so nothing was changed.")
    return value


def _write(path: Path, value: Mapping[str, Any], mode: int) -> None:
    """``value`` as JSON at ``path``, written whole or not at all."""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2)
            handle.write("\n")
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def install(
    *,
    scope: str,
    config: dict[str, Any],
    python: str,
    config_path: Path | None,
    settings_path: Path | None,
    selector: tuple[str, str] | None,
    out: TextIO,
    runtime: str = claude_code.CLI,
) -> None:
    """Write the configuration and the hook entries. Raises :class:`InstallError`
    with nothing changed when either cannot be written."""
    default_config, default_settings = default_paths(scope, runtime)
    config_file = config_path or default_config
    settings_file = settings_path or default_settings
    entries = _ENTRIES[runtime](python=python, config_path=str(config_file), selector=selector)
    if _whole_file(scope, runtime):
        settings: dict[str, Any] = dict(entries)
    else:
        settings = merged(_read_settings(settings_file), entries, runtime)
    for target in (config_file, settings_file):
        folder = target.parent
        while not folder.exists():
            folder = folder.parent
        if not os.access(folder if not target.exists() else target, os.W_OK):
            hint = " Run it with sudo." if scope == "managed" else ""
            raise InstallError(f"{target} cannot be written, so nothing was changed.{hint}")

    _write(config_file, config, 0o644 if scope == "managed" else 0o600)
    if settings_file.exists() and not _whole_file(scope, runtime):
        backup = settings_file.with_name(settings_file.name + BACKUP_SUFFIX)
        if not backup.exists():
            backup.write_bytes(settings_file.read_bytes())
    _write(settings_file, settings, 0o644)

    q = shlex.quote
    name = _NAMES[runtime]
    lines = [
        f"Wrote the hook configuration: {config_file}",
        f"Wrote the {name} hook entries: {settings_file}",
    ]
    if "keychain" in config:
        item = config["keychain"]
        lines += [
            "",
            "Connected. Store the Gate credential in your login keychain. This asks for",
            f"it and does not show it. Run it as the person who runs {name}, not with sudo:",
            f"  security add-generic-password -U -s {q(item['service'])} -a {q(item['account'])} -w",
        ]
    else:
        lines += [
            "",
            "Standalone: every tool call is allowed and recorded on the local Track,",
            "~/.bindable/local-track.jsonl. Nothing is sent to Bindable. To connect, run this",
            "again with --agent-id and --base-url.",
        ]
    if runtime == codex_cli.CLI:
        lines += [
            "",
            "Codex runs a new or changed hook only after you trust it. Start Codex, run",
            "/hooks, and trust the four Bindable Gate hooks. Until then no tool call is",
            "reviewed, and the session shows no Bindable Gate line.",
            "",
            "A Codex session can edit hooks.json, and [features] hooks = false in",
            "config.toml turns every hook off. Codex's hooks do not see its hosted tools,",
            "such as web search.",
        ]
    elif runtime == cursor.CLI:
        lines += [
            "",
            "Cursor runs project hooks only in a trusted workspace. It also runs Claude Code's",
            "hooks from ~/.claude/settings.json unless \"Include Third-Party Plugins, Skills, and",
            "Other Configs\" is off, so if the Claude Code Gate is installed there, Cursor's",
            "calls are reviewed twice.",
        ]
        if scope == "user":
            lines += ["A Cursor session can edit ~/.cursor/hooks.json; --scope managed under sudo",
                      "writes the enterprise hooks.json instead."]
        lines += _claude_code_also_installed(cursor.CLI)
    elif runtime == gemini_cli.CLI:
        lines += [
            "",
            "Gemini CLI runs hooks only in a trusted folder, and hooksConfig.enabled = false",
            "in settings.json turns them off. Gemini CLI's own policy and confirmation still",
            "apply to every call the Gate releases.",
        ]
    elif runtime == windsurf.CLI:
        lines += [
            "",
            "These hooks are for Devin Local, the agent Windsurf (Devin Desktop) runs now.",
            "Cascade, its earlier agent, starts no new conversations. Devin runs no hook while",
            "a workspace is in Restricted Mode, and it also runs Claude Code's hooks from",
            "~/.claude/settings.json, so if the Claude Code Gate is installed there, its calls",
            "are reviewed twice. A Devin session can edit ~/.config/devin/config.json.",
        ]
        lines += _claude_code_also_installed(windsurf.CLI)
    elif runtime == copilot_cli.CLI and scope == "user":
        lines += [
            "",
            "Scope user: a Copilot CLI session can edit this file, and disableAllHooks turns",
            "it off. To install where a session cannot change it, run with --scope managed",
            "under sudo: the policy file then runs before every other hook.",
        ]
    elif scope == "user":
        lines += [
            "",
            "Scope user: a Claude Code session can edit ~/.claude/settings.json. To install",
            "where a session cannot change it, run with --scope managed under sudo.",
        ]
    lines += ["", f"Start a new {name} session for the hook to take effect."]
    out.write("\n".join(lines) + "\n")


def _claude_code_also_installed(runtime: str) -> list[str]:
    """A warning when the Claude Code Gate is in ~/.claude/settings.json, which
    ``runtime`` also reads."""
    try:
        settings = _read_settings(user_settings_path())
    except InstallError:
        return []
    if not any(_ours(group, claude_code.CLI) for groups in (settings.get("hooks") or {}).values()
               if isinstance(groups, list) for group in groups):
        return []
    return ["", f"Warning: the Claude Code Gate is installed in {user_settings_path()}, which",
            f"{_NAMES[runtime]} also runs. Each call will be reviewed by both."]


def uninstall(
    *,
    scope: str,
    config_path: Path | None,
    settings_path: Path | None,
    out: TextIO,
    runtime: str = claude_code.CLI,
) -> None:
    """Remove what :func:`install` wrote, and nothing else."""
    default_config, default_settings = default_paths(scope, runtime)
    config_file = config_path or default_config
    settings_file = settings_path or default_settings
    keychain: Mapping[str, Any] | None = None
    if config_file.is_file():
        try:
            raw = json.loads(config_file.read_text(encoding="utf-8"))
            keychain = raw.get("keychain") if isinstance(raw, dict) else None
        except (OSError, ValueError):
            keychain = None
    if _whole_file(scope, runtime):
        settings_file.unlink(missing_ok=True)
    elif settings_file.exists():
        _write(settings_file, without_ours(_read_settings(settings_file), runtime), 0o644)
    config_file.unlink(missing_ok=True)
    lines = [f"Removed the hook entries from {settings_file}", f"Removed {config_file}"]
    if isinstance(keychain, Mapping):
        q = shlex.quote
        lines += [
            "To remove the Gate credential from your keychain as well:",
            (f"  security delete-generic-password -s {q(str(keychain.get('service')))} "
             f"-a {q(str(keychain.get('account')))}"),
        ]
    out.write("\n".join(lines) + "\n")


__all__ = [
    "DEFAULT_KEYCHAIN_SERVICE",
    "RUNTIMES",
    "SCOPES",
    "InstallError",
    "hook_configuration",
    "install",
    "merged",
    "uninstall",
    "without_ours",
]
