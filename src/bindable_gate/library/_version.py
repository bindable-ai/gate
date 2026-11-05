# bindable-gate/src/bindable_gate/library/_version.py
"""The version a report carries.

This package's version, not the thin client's. The Library Gate ships here now,
so the version on a Trackpoint must name the distribution that holds the code
that enforced; naming `bindable` would point at a package that no longer carries
the Gate.

Read from installed metadata rather than a literal, so it cannot drift from what
was published. `core/client.py` already has a function that does this, and it is
not imported: that module imports `ActionGate` from the thin client, which the
Library's own release removes, and the Library must not acquire a dependency on a
name that is going away.
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version


def library_version() -> str:
    try:
        return version("bindable-gate")
    except PackageNotFoundError:  # pragma: no cover - a source tree with no install
        return "0.0.0+unknown"
