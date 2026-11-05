# bindable-gate/src/bindable_gate/library/_value.py
"""The one check the Library makes on an Evidence value before sending it.

Evidence is canonical JSON with no floats. A float is refused here
rather than converted, because converting is the Library deciding what the
developer meant: 0.1 is not 1/10 in binary, and a Contract rule that compares a
number would be evaluating a value nobody wrote. The refusal names where the
float is, so a nested one in a large payload is findable.

Anything else that is not JSON is refused the same way and for the same reason:
a ``Decimal``, a ``datetime``, a set, bytes, an object, or a mapping whose key
is not a string. Left alone, each would reach the JSON encoder and surface as a
raw ``TypeError`` from inside the HTTP client, naming nothing; or, for an
integer key, be silently turned into a string the developer never wrote.

This is a validation and not canonicalisation. Nothing here serialises, orders,
hashes or signs anything.
"""

from __future__ import annotations

from typing import Any

#: The kinds a JSON value is made of, floats excepted.
_SCALARS = (str, int, bool, type(None))


def _walk(value: Any, path: str) -> tuple[str, str] | None:
    """The path and kind of the first value that is not JSON, depth first, or
    ``None``."""
    if isinstance(value, float):
        return path, "float"
    if isinstance(value, _SCALARS):
        return None
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                return path, f"{type(key).__name__} key"
            found = _walk(item, f"{path}.{key}" if path else key)
            if found is not None:
                return found
        return None
    if isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            found = _walk(item, f"{path}[{index}]")
            if found is not None:
                return found
        return None
    return path, type(value).__name__


def refused_at(value: Any) -> tuple[str, str] | None:
    """Where the value holds something that is not JSON with no floats, and
    what it is: ``("amount", "float")``, ``("fee", "Decimal")``. ``None`` when
    the whole value can be sent.

    ``True`` and ``False`` are JSON and are left alone; ``1.0`` is a float,
    whole number or not, because what reaches the wire is a float either way.
    """
    return _walk(value, "")


def float_at(value: Any) -> str | None:
    """Where the value holds a float, or ``None`` when it holds none."""
    found = refused_at(value)
    return found[0] if found is not None and found[1] == "float" else None
