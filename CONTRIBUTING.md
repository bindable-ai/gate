# Contributing

Thank you for helping. This repository holds the released Bindable Gates: the
Runtime Plugin Gate for OpenClaw and NemoClaw (`packages/gate-openclaw`), the
TypeScript core it runs on (`packages/gate`) and the Python core
(`bindable-gate`).

## Issues

Two kinds of issue are welcome:
- **Accountability issue:** the Gate let an action run that it should have
  held, held one it should have let run, or recorded it wrongly.
- **Bug fix:** anything else that does not work as the docs say.

For a security problem, follow [SECURITY.md](SECURITY.md) instead.

## Pull requests

- Keep a pull request to one change, with a test that fails without it.
- Run the checks before you open it:

  ```bash
  npm ci && npm run build && npm test && npm run typecheck
  python3.11 -m venv .venv && .venv/bin/pip install -e . && .venv/bin/python -c "import bindable_gate"
  ```

- Never put a real credential, API key or private key in code, tests or an
  issue. Tests use made-up values.
- By contributing you agree that your contribution is licensed under the
  Apache License 2.0, as the rest of this repository is.

The public repository is updated from Bindable's release process, so an
accepted change appears here in the next release.
