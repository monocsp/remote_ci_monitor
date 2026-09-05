"""이벤트 버스 — 서버 안의 상태 변화를 SSE 구독자와 상태 캐시에 흘린다.

- id 는 1부터 단조 증가. 마지막 `history` 개를 링 버퍼에 두어 `Last-Event-ID` 재연결을 재생한다.
- 구독자마다 bounded queue. 넘치면 가장 오래된 것을 버리고 `lag` 이벤트를 넣는다
  (구독자는 전체 재조회).
- `last_id` 가 링 버퍼 밖이면 `reset` 이벤트 하나를 넣는다(마찬가지로 전체 재조회).
종류와 data 모양은 `docs/m1-workplan.md` 3절.
"""

from __future__ import annotations

import queue
import threading
from collections import deque
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

KIND_JOB_CHANGED = "job_changed"
KIND_JOB_FINISHED = "job_finished"
KIND_MARKER = "marker"
KIND_HOST_SAMPLE = "host_sample"
KIND_SERVER = "server"
KIND_RESET = "reset"
KIND_LAG = "lag"
JOB_KINDS = frozenset({KIND_JOB_CHANGED, KIND_JOB_FINISHED, KIND_MARKER})
DEFAULT_HISTORY = 2048


@dataclass(frozen=True)
class Event:
    id: int
    kind: str
    data: dict[str, Any]
    at: datetime


class Subscription:
    """구독자 하나의 큐. `get(timeout)` 은 타임아웃이면 None."""

    def __init__(self, bus: EventBus, maxsize: int):
        self._bus = bus
        self._queue: queue.Queue[Event] = queue.Queue(maxsize=max(1, maxsize))
        self._lock = threading.Lock()
        self.closed = False

    def get(self, timeout: float | None = None) -> Event | None:
        try:
            return self._queue.get(timeout=timeout)
        except queue.Empty:
            return None

    def _offer(self, event: Event) -> None:
        with self._lock:
            if self.closed:
                return
            try:
                self._queue.put_nowait(event)
                return
            except queue.Full:
                pass
            # 넘쳤다 — 큐를 비우고 lag 하나 + 방금 이벤트만 남긴다(구독자는 전체 재조회)
            while True:
                try:
                    self._queue.get_nowait()
                except queue.Empty:
                    break
            lag = Event(id=event.id, kind=KIND_LAG, data={}, at=event.at)
            for e in (lag, event):
                try:
                    self._queue.put_nowait(e)
                except queue.Full:
                    break

    def close(self) -> None:
        with self._lock:
            self.closed = True


class EventBus:
    def __init__(self, history: int = DEFAULT_HISTORY):
        self._lock = threading.Lock()
        self._subs: list[Subscription] = []
        self._ring: deque[Event] = deque(maxlen=max(1, history))
        self._last_id = 0

    @property
    def last_id(self) -> int:
        with self._lock:
            return self._last_id

    @property
    def subscriber_count(self) -> int:
        with self._lock:
            return len(self._subs)

    def publish(self, kind: str, data: dict[str, Any], *, at: datetime | None = None) -> Event:
        with self._lock:
            self._last_id += 1
            event = Event(id=self._last_id, kind=kind, data=dict(data), at=at or datetime.now(UTC))
            self._ring.append(event)
            subs = list(self._subs)
        for sub in subs:
            sub._offer(event)
        return event

    def subscribe(self, *, last_id: int | None = None, maxsize: int | None = None) -> Subscription:
        sub = Subscription(self, maxsize or self._ring.maxlen or DEFAULT_HISTORY)
        with self._lock:
            replay: list[Event] = []
            if last_id is not None and last_id < self._last_id:
                oldest = self._ring[0].id if self._ring else self._last_id + 1
                if last_id + 1 >= oldest:
                    replay = [e for e in self._ring if e.id > last_id]
                else:
                    replay = [
                        Event(id=self._last_id, kind=KIND_RESET, data={}, at=datetime.now(UTC))
                    ]
            self._subs.append(sub)
        for e in replay:
            sub._offer(e)
        return sub

    def unsubscribe(self, sub: Subscription) -> None:
        sub.close()
        with self._lock:
            try:
                self._subs.remove(sub)
            except ValueError:
                pass

    def shutdown(self) -> None:
        """서버 종료 — 모든 구독자를 깨워 SSE 루프가 빠져나가게 한다."""
        self.publish(KIND_SERVER, {"shutdown": True})
        with self._lock:
            subs = list(self._subs)
        for sub in subs:
            sub.close()
