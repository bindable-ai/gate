# bindable-gate/src/bindable_gate/library/errors.py
"""What the Library Gate raises, reconciled with what this package already had.

The Library came from `bindable-ai/bindable` with its own five errors, and four
of them already existed here under the same names and the same meanings, so they
are reused rather than shadowed. A second `GateBlocked` meaning the same thing
would be worse than no port at all.

| From the Library      | Here                                    |
|-----------------------|-----------------------------------------|
| `BindableError` base  | `GateError`, the base this package has  |
| `GateConfigError`     | the same, unchanged                     |
| `GateBlocked`         | the same, unchanged                     |
| `GateUnreachable`     | the same, and `ProposalUnreachable` below |
| `TrackpointNotRecorded` | the same, unchanged                   |
| `BindableAPIError`    | `ProposalRefused` below                 |

Two are added, and both are subclasses so that nothing in `core/errors.py`
changes — the MCP Gate imports that module and must stay green.

`ProposalUnreachable` is a `GateUnreachable` that also carries the identifiers of
the Proposal that did not complete, so a caller can present the same ones again
and collect the Receipt the lost attempt may already have minted.
`core.errors.GateUnreachable` has nowhere to put them and is not widened.

`ProposalRefused` is a `GateError`, where the thin client's `BindableAPIError` is
a plain `RuntimeError`. The Library's own property is that **every** error it
raises means the action did not run and one `except` catches them all; a refusal
escaping `except GateError` would break that, and the refusal is this Gate's own
answer rather than the thin client's, this Gate making its own calls.
"""

from __future__ import annotations

from bindable_gate.core.errors import GateError, GateUnreachable


class ProposalUnreachable(GateUnreachable):
    """No Receipt could be obtained, and here is what to present on a retry.

    Attributes:
        proposed_action_id: The Proposal's own identifier, to reuse on retry.
        nonce: The Proposal's nonce, to reuse on retry.
        proposed_at: The time the Proposal was first made.
        no_answer: True when no attempt got any answer at all, which is what
            offline mode counts as Bindable being unreachable. An answer that
            could not be read, or that carried no Receipt, is an answer.
    """

    def __init__(
        self,
        detail: str,
        *,
        proposed_action_id: str = "",
        nonce: str = "",
        proposed_at: str = "",
        no_answer: bool = False,
    ) -> None:
        super().__init__(detail)
        self.proposed_action_id = proposed_action_id
        self.nonce = nonce
        self.proposed_at = proposed_at
        self.no_answer = no_answer


class GateOffline(ProposalUnreachable):
    """Bindable is unreachable and the agent's setting is ask me first, so nothing ran.

    The action will not run until the person runs ``bindable-gate offline
    accept`` on this machine. Its message is the one the person is shown, whole. A kind of
    :class:`ProposalUnreachable`, so code that already handles "no Receipt,
    so nothing ran" handles this too.
    """

    def __init__(self, message: str, *, proposed_action_id: str = "", nonce: str = "",
                 proposed_at: str = "") -> None:
        super().__init__(message, proposed_action_id=proposed_action_id, nonce=nonce,
                         proposed_at=proposed_at, no_answer=True)
        self.args = (message,)


class ProposalRefused(GateUnreachable):
    """The platform answered the Proposal, and the answer was a refusal.

    A kind of :class:`GateUnreachable` on purpose, as `EvidenceNotRecorded`
    already is: no Receipt was obtained, the action did not run, and code that
    already handles "no Receipt, so nothing ran" handles this too without
    learning a new name.

    Attributes:
        status: The HTTP status of the refusal.
        code: The refusal's code, the word on the wire.
        request_id: The platform's request id, to quote when asking about it.
    """

    def __init__(
        self,
        status: int,
        code: str = "",
        detail: str = "",
        request_id: str = "",
    ) -> None:
        super().__init__(
            f"the Proposal was refused: {status} {code or detail or 'refused'}"
            + (f" (request {request_id})" if request_id else "")
        )
        self.status = status
        self.code = code
        self.request_id = request_id


class EvidenceValueRefused(GateError):
    """The Evidence value holds a float or a non-JSON value, so nothing was sent.

    Evidence is canonical JSON with no floats. The float is refused
    here rather than converted: converting would be this Library deciding what
    the developer meant, and a Contract rule comparing the result would be
    evaluating a number nobody wrote. A value JSON cannot carry, a ``Decimal``
    or a ``datetime`` or a set, is refused the same way, by where it is, rather
    than reaching the encoder as a ``TypeError`` that names nothing.

    Raised before the request, so no Evidence exists and no idempotency key has
    been spent.

    Attributes:
        path: Where it is inside the value, such as ``amount`` or
            ``items[2].price``. The empty string when it is the whole value.
        kind: What it is: ``float``, or the type's name, such as ``Decimal``,
            or ``int key`` for a mapping key that is not a string.
    """

    def __init__(self, path: str, kind: str = "float") -> None:
        where = f"at {path!r}" if path else "as the whole value"
        if kind == "float":
            message = (
                f"the Evidence value holds a float {where}; Evidence is canonical "
                "JSON with no floats, and this Library will not convert it for you"
            )
        else:
            message = (
                f"the Evidence value holds a {kind} {where}, which JSON cannot "
                "carry; nothing was sent, and this Library will not convert it for you"
            )
        super().__init__(message)
        self.path = path
        self.kind = kind


class EvidenceRefused(GateError):
    """The platform answered the Evidence submission, and refused it.

    Never a success. ``external_id_exists`` in particular means an Evidence
    record already holds that identity, and treating it as though the
    submission had worked would let a caller believe it wrote something it did
    not.

    Attributes:
        status: The HTTP status of the refusal.
        code: The refusal's code, the word on the wire.
        request_id: The platform's request id, to quote when asking about it.
    """

    def __init__(
        self,
        status: int,
        code: str = "",
        detail: str = "",
        request_id: str = "",
    ) -> None:
        super().__init__(
            f"the Evidence was refused: {status} {code or detail or 'refused'}"
            + (f" (request {request_id})" if request_id else "")
        )
        self.status = status
        self.code = code
        self.request_id = request_id


class EvidenceUnreachable(GateUnreachable):
    """The Evidence submission got no answer, and here is what to retry with.

    The idempotency key is carried because it is what makes the retry safe: the
    same key on the same call is answered with whatever the first attempt did,
    rather than writing a second Evidence record for one submission.

    Attributes:
        idempotency_key: The key the attempts presented, to present again.
    """

    def __init__(self, detail: str, *, idempotency_key: str = "") -> None:
        super().__init__(detail)
        self.idempotency_key = idempotency_key


__all__ = [
    "EvidenceRefused",
    "EvidenceUnreachable",
    "EvidenceValueRefused",
    "ProposalRefused",
    "ProposalUnreachable",
]
