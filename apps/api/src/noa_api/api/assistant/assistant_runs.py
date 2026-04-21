from __future__ import annotations

import asyncio
from copy import deepcopy
from collections.abc import AsyncGenerator, Callable, Coroutine, Mapping
from dataclasses import dataclass, field
from typing import Any, cast
from uuid import UUID

from noa_api.api.assistant.assistant_run_stream import (
    build_run_delta_event,
    build_run_snapshot_event,
)


RunSnapshot = dict[str, object]
RunEvent = dict[str, object]
_STREAM_CLOSED = object()


@dataclass(slots=True, eq=False)
class _Subscriber:
    queue: asyncio.Queue[RunEvent | object] = field(default_factory=asyncio.Queue)


@dataclass(slots=True)
class _TrackedRun:
    token: object
    task: asyncio.Task[object]


class AssistantRunCoordinator:
    def __init__(self, *, instance_id: str) -> None:
        self.instance_id = instance_id
        self._tasks: dict[UUID, _TrackedRun] = {}
        self._snapshots: dict[UUID, RunSnapshot] = {}
        self._latest_events: dict[UUID, RunEvent] = {}
        self._sequences: dict[UUID, int] = {}
        self._subscribers: dict[UUID, list[_Subscriber]] = {}

    def _publish_snapshot(
        self, *, run_id: UUID, owner_token: object, snapshot: Mapping[str, object]
    ) -> RunEvent | None:
        tracked_run = self._tasks.get(run_id)
        if tracked_run is None or tracked_run.token is not owner_token:
            return None
        sequence = self._sequences.get(run_id, 0) + 1
        self._sequences[run_id] = sequence
        payload = deepcopy(dict(snapshot))
        self._snapshots[run_id] = payload
        if sequence == 1:
            event = build_run_snapshot_event(sequence=sequence, snapshot=payload)
        else:
            event = build_run_delta_event(sequence=sequence, snapshot=payload)
        self._latest_events[run_id] = deepcopy(event)
        for subscriber in list(self._subscribers.get(run_id, [])):
            subscriber.queue.put_nowait(deepcopy(event))
        return deepcopy(event)

    def start_detached_run(
        self,
        *,
        run_id: UUID,
        job_factory: Callable[[AssistantRunHandle], Coroutine[Any, Any, object]],
    ) -> AssistantRunHandle:
        if run_id in self._tasks:
            raise ValueError(f"Run {run_id} is already tracked")

        owner_token = object()
        handle = AssistantRunHandle(
            run_id=run_id,
            coordinator=self,
            owner_token=owner_token,
        )
        task = asyncio.create_task(job_factory(handle), name=f"assistant-run-{run_id}")
        handle.task = task
        self._tasks[run_id] = _TrackedRun(token=owner_token, task=task)
        return handle

    def get_snapshot(self, *, run_id: UUID) -> RunSnapshot | None:
        snapshot = self._snapshots.get(run_id)
        return None if snapshot is None else deepcopy(snapshot)

    def has_run(self, *, run_id: UUID) -> bool:
        return run_id in self._tasks

    def remove_run(self, *, run_id: UUID) -> None:
        tracked_run = self._tasks.pop(run_id, None)
        if tracked_run is not None and not tracked_run.task.done():
            tracked_run.task.cancel()
        self._snapshots.pop(run_id, None)
        self._latest_events.pop(run_id, None)
        self._sequences.pop(run_id, None)
        subscribers = self._subscribers.pop(run_id, [])
        for subscriber in subscribers:
            while True:
                try:
                    _ = subscriber.queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
            subscriber.queue.put_nowait(_STREAM_CLOSED)

    async def wait_for_run(
        self, *, run_id: UUID, timeout: float | None = None
    ) -> object:
        tracked_run = self._tasks.get(run_id)
        if tracked_run is None:
            raise KeyError(run_id)
        if timeout is None:
            return await tracked_run.task
        return await asyncio.wait_for(asyncio.shield(tracked_run.task), timeout=timeout)

    async def subscribe(self, *, run_id: UUID) -> AsyncGenerator[RunEvent, None]:
        if run_id not in self._tasks:
            return

        subscriber = _Subscriber()
        listeners = self._subscribers.setdefault(run_id, [])
        listeners.append(subscriber)

        snapshot = self._snapshots.get(run_id)
        sequence = self._sequences.get(run_id)
        if snapshot is not None and sequence is not None:
            subscriber.queue.put_nowait(
                build_run_snapshot_event(sequence=sequence, snapshot=snapshot)
            )

        try:
            while True:
                item = await subscriber.queue.get()
                if item is _STREAM_CLOSED:
                    break
                yield deepcopy(cast(RunEvent, item))
        finally:
            current_listeners = self._subscribers.get(run_id)
            if current_listeners is None:
                return
            self._subscribers[run_id] = [
                existing for existing in current_listeners if existing is not subscriber
            ]
            if not self._subscribers[run_id]:
                self._subscribers.pop(run_id, None)


@dataclass(slots=True)
class AssistantRunHandle:
    run_id: UUID
    coordinator: AssistantRunCoordinator
    owner_token: object
    task: asyncio.Task[object] = field(init=False)

    def publish_snapshot(self, *, snapshot: Mapping[str, object]) -> RunEvent | None:
        return self.coordinator._publish_snapshot(
            run_id=self.run_id,
            owner_token=self.owner_token,
            snapshot=snapshot,
        )
