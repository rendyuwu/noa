"""Driving a CHANGE runner from a test, on either side of the seam.

A runner answers a `ChangeOutcome` — the tool envelope, and the before→after delta beside it
(`core.approvals.delta`). Most assertions in this suite are about one or the other, not both, so
reaching for `.payload` at every call site would be noise in every file that predates the delta
and repetition in every file that does not.

Two entry points, and the split is deliberate rather than a convenience:

- `payload_runner` hands back a callable that answers the envelope, which is what every
  behavioural claim about a runner is about — the code it names, the sentence an operator reads,
  the commands it sent.
- `delta_of` and `outcome_of` reach the other half, and they are separate names so a test that
  means to assert on the delta cannot be one that forgot to.

`payload_runner` accepts a bare-envelope runner too, because the union `ChangeRunner` declares
is part of the contract: a runner with nothing to state may answer the dict.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from core.approvals.delta import ChangeDelta, ChangeOutcome
from core.approvals.execution import ChangeExecutionRequest, ChangeRunner

PayloadRunner = Callable[[ChangeExecutionRequest], Awaitable[dict[str, object]]]


async def outcome_of(runner: ChangeRunner, request: ChangeExecutionRequest) -> ChangeOutcome:
    """One run, as a `ChangeOutcome` whichever shape the runner answered in.

    The same normalisation `core.approvals.execution` makes, and made here rather than assumed
    so a test drives the runner exactly as the executor does.
    """
    answered = await runner(request)
    return answered if isinstance(answered, ChangeOutcome) else ChangeOutcome(payload=answered)


async def delta_of(runner: ChangeRunner, request: ChangeExecutionRequest) -> ChangeDelta | None:
    """The delta one run published, or `None` when it published none.

    `None` is an assertable answer and not a gap: it is what an executor refusal and a runner
    that measured nothing both look like, and telling those apart from a rich failure is the
    whole reason the delta rides beside the envelope.
    """
    return (await outcome_of(runner, request)).delta


def payload_runner(runner: ChangeRunner) -> PayloadRunner:
    """`runner`, answering only its envelope.

    For the assertions that are about the envelope. The delta is dropped rather than merged in,
    because merging it would put the delta's keys where a byte-identity claim about the payload
    is made — which is the thing the seam exists to keep possible.
    """

    async def run(request: ChangeExecutionRequest) -> dict[str, object]:
        return (await outcome_of(runner, request)).payload

    return run


__all__ = ["PayloadRunner", "delta_of", "outcome_of", "payload_runner"]
