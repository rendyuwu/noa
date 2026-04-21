from __future__ import annotations

import json
from copy import deepcopy
from collections.abc import Mapping


RunEvent = dict[str, object]


def build_run_snapshot_event(
    *, sequence: int, snapshot: Mapping[str, object]
) -> RunEvent:
    return {
        "type": "snapshot",
        "sequence": sequence,
        "snapshot": deepcopy(dict(snapshot)),
    }


def build_run_delta_event(*, sequence: int, snapshot: Mapping[str, object]) -> RunEvent:
    return {
        "type": "delta",
        "sequence": sequence,
        "snapshot": deepcopy(dict(snapshot)),
    }


def encode_sse_event(*, event: Mapping[str, object]) -> bytes:
    event_type = str(event.get("type", "message"))
    payload = json.dumps(deepcopy(dict(event)), separators=(",", ":"))
    return f"event: {event_type}\ndata: {payload}\n\n".encode()
