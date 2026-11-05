# bindable-gate/src/bindable_gate/__main__.py
"""``python -m bindable_gate``: the ``bindable-gate`` command.

With no command it runs the local MCP proxy. ``install``, ``uninstall``,
``track`` and ``hooks`` are the commands in :mod:`bindable_gate.cli`.
"""

import sys

from bindable_gate.cli import main

sys.exit(main())
