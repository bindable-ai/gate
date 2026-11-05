# bindable-gate/src/bindable_gate/contract/canonical.py
"""RFC 8785 canonical JSON, and SHA-256 over it.

Canonicalisation is the maintained ``rfc8785`` library's, not a copy. The
TypeScript core uses ``canonicalize``; both implement RFC 8785, so the same
value gives the same bytes in either.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

import rfc8785


def to_json(value: Any) -> Any:
    """``value`` as plain JSON: what ``json`` can write, with anything else
    written as its ``str``. Tuples become lists."""
    return json.loads(json.dumps(value, default=str, ensure_ascii=False))


def canonical_json(value: Any) -> str:
    """The RFC 8785 canonical JSON of ``value``, after :func:`to_json`.

    Raises:
        ValueError: The value has no canonical form, for example an integer
            beyond what a JSON number can hold exactly, or NaN.
    """
    try:
        return rfc8785.dumps(to_json(value)).decode("utf-8")
    except (rfc8785.CanonicalizationError, ValueError, TypeError) as error:
        raise ValueError("the value has no canonical JSON form") from error


def sha256_hex(text: str) -> str:
    """Lower-case hex SHA-256 of a string's UTF-8 bytes."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def canonical_sha256(value: Any) -> str:
    """Lower-case hex SHA-256 of the canonical JSON of ``value``."""
    return sha256_hex(canonical_json(value))
