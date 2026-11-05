# bindable-gate/src/bindable_gate/library/payload.py
"""What a Proposal's payload may carry: named fields only.

The Library Gate and the agent hook build their payloads here, so the rule is
written once. A payload carries the fields its caller names and nothing else.
Every other value stays in the process that holds it.

``mask_credentials`` replaces the shapes of common credentials in a piece of
text with ``***``. The agent hook applies it to text it does not control, such
as a shell command. It is a second line of defence, never a licence to name a
field that holds a secret: name only what your rules need.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any, Final

#: What a masked credential becomes.
MASK: Final[str] = "***"

#: Credential shapes. A pattern with a group keeps the group, the prefix that
#: names the credential, and masks what follows it.
_CREDENTIAL_SHAPES: Final[tuple[re.Pattern[str], ...]] = (
    re.compile(r"bnd_[a-z]{2}_[A-Za-z0-9_\-]+"),
    re.compile(r"gh[pousr]_[A-Za-z0-9]+"),
    re.compile(r"github_pat_[A-Za-z0-9_]+"),
    re.compile(r"sk-[A-Za-z0-9_\-]{16,}"),
    re.compile(r"AKIA[A-Z0-9]{16}"),
    re.compile(r"(x-access-token:)[^@\s]+"),
    re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=\-]+"),
    re.compile(r"(?i)(://[^/\s:@]+:)[^@\s]+(?=@)"),
    re.compile(
        r"(?i)\b([A-Z0-9_]*(?:TOKEN|SECRET|PASSWORD|PASSWD|API_KEY|APIKEY|CREDENTIAL)"
        r"[A-Z0-9_]*\s*[=:]\s*)(\"[^\"]*\"|'[^']*'|\S+)"
    ),
)


def mask_credentials(text: str) -> str:
    """``text`` with every credential shape replaced by ``***``."""
    for shape in _CREDENTIAL_SHAPES:
        if shape.groups:
            text = shape.sub(lambda match: match.group(1) + MASK, text)
        else:
            text = shape.sub(MASK, text)
    return text


def without_secret(value: Any, secret: str) -> Any:
    """A copy of ``value`` with ``secret`` replaced by ``***`` in every piece of
    text, keys included.

    The Gate credential can reach a payload: a tool argument that echoes the
    environment, or, inside an OpenShell sandbox, the placeholder that stands
    for the credential. Whatever its form, only the ``Authorization`` header
    carries it.
    """
    if not secret:
        return value
    if isinstance(value, str):
        return value.replace(secret, MASK)
    if isinstance(value, Mapping):
        return {without_secret(k, secret): without_secret(v, secret) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [without_secret(v, secret) for v in value]
    return value


def named_fields(
    values: Mapping[str, Any],
    fields: Sequence[str],
    *,
    mask: bool = False,
    limits: Mapping[str, int] | None = None,
) -> dict[str, Any]:
    """The named fields of ``values`` that are present, and nothing else.

    Args:
        values: Where the fields are read from.
        fields: The names to carry, in order.
        mask: Replace credential shapes in each text value with ``***``.
        limits: The most characters to keep of a text value, by field name.
            A value is masked before it is cut, so a credential that crosses
            the cut is masked whole.

    Returns:
        A new dict holding only the named fields ``values`` has.
    """
    limits = limits or {}
    carried: dict[str, Any] = {}
    for name in fields:
        if name not in values:
            continue
        value = values[name]
        if isinstance(value, str):
            if mask:
                value = mask_credentials(value)
            if name in limits:
                value = value[: limits[name]]
        carried[name] = value
    return carried
