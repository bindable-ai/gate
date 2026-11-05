# bindable-gate/src/bindable_gate/hooks/credential.py
"""The agent hook's Gate credential, read from the macOS keychain.

The credential is never in a file in the user's home. The administrator adds
it to the login keychain of the person who runs the agent, as a generic
password item named by service and account in the hook's configuration:

    security add-generic-password -U -s SERVICE -a ACCOUNT -w

The hook reads it with macOS's own ``/usr/bin/security`` on each call and keeps
it only in memory. ``security`` created the item, so the item's access list
trusts it and the read shows no dialog. A dialog would have no one to answer
it and would hold the tool call until the deadline. A read through the Security
framework from Python can wait on one, which is why this module does not use
that framework.

LIMIT. Any process of the same user can run ``security`` too, the governed
session included. The keychain keeps the credential out of every file. It does
not keep it from a session that sets out to read it. A helper that runs as
another user is what closes that.
"""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from typing import Final

#: macOS's keychain tool, by absolute path, so nothing on PATH can stand in.
SECURITY_TOOL: Final[str] = "/usr/bin/security"

#: Seconds the read may take.
READ_TIMEOUT_SECONDS: Final[float] = 5.0

_NOT_FOUND: Final[int] = 44  # security: "The specified item could not be found"


class CredentialUnavailable(Exception):
    """The Gate credential could not be read. The message is one plain sentence."""


@dataclass(frozen=True)
class KeychainItem:
    """A generic password item, named by its service and account."""

    service: str
    account: str

    def __str__(self) -> str:
        return f"service {self.service!r}, account {self.account!r}"


CredentialReader = Callable[[KeychainItem], str]


def read_keychain(item: KeychainItem) -> str:
    """The item's password, or :class:`CredentialUnavailable`.

    Raises:
        CredentialUnavailable: Not on macOS, no such item, the read was
            refused or took too long, or the item is empty.
    """
    if sys.platform != "darwin":
        raise CredentialUnavailable(
            "The agent hook reads its credential from the macOS keychain, "
            "and this computer is not running macOS."
        )
    try:
        result = subprocess.run(
            [SECURITY_TOOL, "find-generic-password", "-s", item.service, "-a", item.account, "-w"],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=READ_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired:
        raise CredentialUnavailable(
            f"The keychain did not give the item with {item} within "
            f"{READ_TIMEOUT_SECONDS:g} seconds. Check that the keychain is unlocked."
        ) from None
    except OSError:
        raise CredentialUnavailable(
            f"The hook could not run {SECURITY_TOOL} to read the keychain."
        ) from None
    if result.returncode == _NOT_FOUND:
        raise CredentialUnavailable(
            f"The keychain has no item with {item}. "
            "Ask your administrator to add the Gate credential."
        )
    if result.returncode != 0:
        raise CredentialUnavailable(
            f"The keychain refused to give the hook the item with {item} "
            f"(security exit {result.returncode}). Unlock the keychain, or check the item."
        )
    secret = result.stdout.strip()
    if not secret:
        raise CredentialUnavailable(f"The keychain item with {item} is empty.")
    return secret
