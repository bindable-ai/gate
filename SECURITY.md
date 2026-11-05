# Security

## Reporting a vulnerability

Email **security@bindable-ai.com**. Please do not open a public issue for a
vulnerability.

Tell us what you found, how to reproduce it, and which package and version it
affects (`@bindable-ai/gate-openclaw`, `@bindable-ai/gate` or `bindable-gate`).
We reply within three working days, and we tell you when a fix is released.

Never include a real Gate credential, API key or private key in a report.
If a credential leaks, rotate it in the Portal or with the API, and email security@bindable-ai.com.

## What counts

Anything that lets a tool call run without the review the Gate should give it,
for example:
- a call that runs although Bindable blocked it, or was never asked;
- a Gate credential or agent key that reaches a log, a file, a Proposal or any
  place other than the request to Bindable;
- a way to change or remove a record in the local Track or an offline record
  without the chain showing it.

## Supported versions

The latest release of each package.
