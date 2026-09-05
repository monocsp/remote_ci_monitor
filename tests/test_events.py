"""EventBus — 단조 id · 구독/발행 · get 타임아웃 · Last-Event-ID 재생 · 링 버퍼 밖 reset ·
큐 넘침 lag · 구독 해제 · 스레드 안전성. 명세는 docs/m1-workplan.md 3절.

시각은 고정 T0 기준으로 넘긴다(버스는 시계를 보지 않는다). 기다림은 전부 마감이 있는 get 이다.
"""

import threading
import time
from datetime import UTC, datetime, timedelta

import pytest

from remote_ci_monitor.events import Event, EventBus

T0 = datetime(2026, 9, 5, 0, 0, 0, tzinfo=UTC)


def at(n: int) -> datetime:
    return T0 + timedelta(seconds=n)


def publish_n(bus: EventBus, n: int, *, start: int = 0) -> list[Event]:
    """job_changed 를 n 개 발행한다. data.n 으로 어느 것인지 알 수 있다."""
    return [
        bus.publish("job_changed", {"job_id": 1, "state": "queued", "n": start + i}, at=at(i))
        for i in range(n)
    ]


def drain(sub, *, timeout: float = 0.05, limit: int = 10_000) -> list[Event]:
    """큐에 지금 들어 있는 것을 전부 꺼낸다(비면 멈춘다)."""
    out: list[Event] = []
    while len(out) < limit:
        ev = sub.get(timeout)
        if ev is None:
            break
        out.append(ev)
    return out


# ── id · Event 모양 ───────────────────────────────────────────────────────────


def test_ids_start_at_1_and_are_monotonic():
    bus = EventBus()
    assert bus.last_id == 0
    first = bus.publish("job_changed", {"job_id": 7, "state": "uploading"}, at=at(0))
    second = bus.publish("marker", {"job_id": 7, "kind": "step", "value": "a"}, at=at(1))
    assert (first.id, second.id) == (1, 2)
    assert bus.last_id == 2
    assert first.kind == "job_changed" and first.data == {"job_id": 7, "state": "uploading"}
    assert first.at == at(0) and second.at == at(1)
    third = bus.publish("server", {"paused": None, "workers": []}, at=at(2))
    assert third.id == 3 and bus.last_id == 3


def test_event_is_immutable():
    bus = EventBus()
    ev = bus.publish("job_changed", {"job_id": 1, "state": "queued"}, at=at(0))
    with pytest.raises(AttributeError):  # frozen dataclass → FrozenInstanceError(AttributeError)
        ev.id = 99  # type: ignore[misc]


# ── 구독 · 발행 ──────────────────────────────────────────────────────────────


def test_subscribe_then_publish_delivers_in_order():
    bus = EventBus()
    sub = bus.subscribe()
    published = publish_n(bus, 3)
    got = [sub.get(1.0) for _ in range(3)]
    assert [e.id for e in got] == [1, 2, 3]
    assert got == published
    assert sub.get(0.05) is None  # 더 없다
    bus.unsubscribe(sub)


def test_get_returns_none_on_timeout_and_waits_about_that_long():
    bus = EventBus()
    sub = bus.subscribe()
    t0 = time.monotonic()
    assert sub.get(0.2) is None
    elapsed = time.monotonic() - t0
    assert 0.15 <= elapsed < 2.0
    bus.unsubscribe(sub)


def test_subscribe_without_last_id_sees_only_future_events():
    bus = EventBus()
    publish_n(bus, 3)
    sub = bus.subscribe()
    assert sub.get(0.05) is None  # 과거는 재생하지 않는다
    ev = bus.publish("job_finished", {"job_id": 1, "state": "succeeded", "exit_code": 0}, at=at(9))
    got = sub.get(1.0)
    assert got is not None and got.id == 4 and got == ev
    bus.unsubscribe(sub)


def test_two_subscribers_each_get_every_event():
    bus = EventBus()
    a = bus.subscribe()
    b = bus.subscribe()
    publish_n(bus, 4)
    assert [e.id for e in drain(a)] == [1, 2, 3, 4]
    assert [e.id for e in drain(b)] == [1, 2, 3, 4]
    bus.unsubscribe(a)
    bus.unsubscribe(b)


# ── Last-Event-ID 재생 ───────────────────────────────────────────────────────


def test_replay_from_last_id_inside_ring_buffer():
    bus = EventBus()
    publish_n(bus, 5)
    sub = bus.subscribe(last_id=3)
    replayed = drain(sub)
    assert [e.id for e in replayed] == [4, 5]
    assert all(e.kind == "job_changed" for e in replayed)  # reset 이 아니다
    live = bus.publish("job_changed", {"job_id": 1, "state": "running"}, at=at(6))
    got = sub.get(1.0)
    assert got is not None and got.id == 6 and got == live
    bus.unsubscribe(sub)


def test_replay_with_current_last_id_replays_nothing():
    bus = EventBus()
    publish_n(bus, 5)
    sub = bus.subscribe(last_id=bus.last_id)
    assert sub.get(0.05) is None  # 놓친 게 없으니 reset 도 재생도 없다
    bus.unsubscribe(sub)


def test_reset_when_last_id_is_older_than_ring_buffer():
    bus = EventBus(history=5)
    publish_n(bus, 12)  # 링 버퍼에는 8..12 만 남는다
    sub = bus.subscribe(last_id=1)
    first = sub.get(1.0)
    assert first is not None and first.kind == "reset" and first.data == {}
    assert sub.get(0.05) is None  # reset 뒤에 옛 이벤트를 흘리지 않는다
    live = bus.publish("job_changed", {"job_id": 2, "state": "queued"}, at=at(20))
    got = sub.get(1.0)
    assert got is not None and got.id == 13 and got == live  # 그 뒤 새 이벤트는 정상
    bus.unsubscribe(sub)
    # 버퍼 안에 있는 id 는 그대로 재생된다(같은 버스, 같은 상태)
    inside = bus.subscribe(last_id=9)
    assert [e.id for e in drain(inside)] == [10, 11, 12, 13]
    bus.unsubscribe(inside)


def test_default_history_is_500():
    bus = EventBus()
    publish_n(bus, 502)  # 버퍼에는 3..502
    sub = bus.subscribe(last_id=3, maxsize=1000)  # 재생 499개가 큐에 다 들어가야 한다
    replayed = drain(sub, limit=1000)
    assert len(replayed) == 499 and replayed[0].id == 4 and replayed[-1].id == 502
    bus.unsubscribe(sub)
    old = bus.subscribe(last_id=1)  # 2 는 밀려났다 → reset
    first = old.get(1.0)
    assert first is not None and first.kind == "reset"
    bus.unsubscribe(old)


# ── 큐 넘침 → lag ────────────────────────────────────────────────────────────


def test_lag_on_overflow_keeps_newest_events():
    bus = EventBus()
    sub = bus.subscribe(maxsize=3)
    publish_n(bus, 10)  # 아무도 안 꺼내는 동안 10개
    drained = drain(sub)
    kinds = [e.kind for e in drained]
    assert kinds.count("lag") == 1, kinds
    lag = next(e for e in drained if e.kind == "lag")
    assert lag.data == {}
    real = [e for e in drained if e.kind != "lag"]
    assert real, "newest events must be retained, not only the lag marker"
    ids = [e.id for e in real]
    # 「가장 오래된 것을 버린다」 → 남는 건 가장 새 것들이다(명세 3절). 옛것을 남기고 새것을
    # 버리면 lag 뒤에 온 이벤트가 구독자에게 영영 안 보인다.
    assert ids[-1] == 10, f"newest event must survive the overflow; kept ids={ids} kinds={kinds}"
    assert ids == sorted(ids) and len(set(ids)) == len(ids)
    assert len(drained) <= 4  # maxsize(3) + lag 하나를 넘지 않는다
    # 넘친 뒤에도 구독은 살아 있다
    nxt = bus.publish("job_changed", {"job_id": 1, "state": "running"}, at=at(11))
    got = sub.get(1.0)
    assert got is not None and got.id == 11 and got == nxt
    bus.unsubscribe(sub)


def test_default_subscription_queue_is_256():
    bus = EventBus()
    sub = bus.subscribe()  # maxsize 기본값 256 (명세 3절 시그니처)
    publish_n(bus, 300)
    drained = drain(sub, limit=1000)
    kinds = [e.kind for e in drained]
    assert "lag" in kinds, "300 events must overflow the default 256-slot queue"
    assert len(drained) <= 257
    assert drained[-1].id == 300
    bus.unsubscribe(sub)


def test_lag_does_not_affect_other_subscribers():
    bus = EventBus()
    small = bus.subscribe(maxsize=2)
    big = bus.subscribe(maxsize=100)
    publish_n(bus, 8)
    assert "lag" in [e.kind for e in drain(small)]
    assert [e.id for e in drain(big)] == list(range(1, 9))
    bus.unsubscribe(small)
    bus.unsubscribe(big)


# ── 구독 해제 ────────────────────────────────────────────────────────────────


def test_unsubscribe_stops_delivery_and_decrements_count():
    bus = EventBus()
    assert bus.subscriber_count == 0
    a = bus.subscribe()
    b = bus.subscribe()
    assert bus.subscriber_count == 2
    bus.unsubscribe(a)
    assert bus.subscriber_count == 1
    ev = bus.publish("job_changed", {"job_id": 1, "state": "queued"}, at=at(0))
    assert a.get(0.05) is None  # 해제된 구독에는 오지 않는다
    got = b.get(1.0)
    assert got is not None and got == ev
    bus.unsubscribe(b)
    assert bus.subscriber_count == 0
    # 구독자가 없어도 발행은 되고 id 는 계속 올라간다
    bus.publish("job_changed", {"job_id": 1, "state": "running"}, at=at(1))
    assert bus.last_id == 2


# ── 스레드 안전성 ────────────────────────────────────────────────────────────


def test_publisher_threads_and_consumer_thread_see_every_id_in_order():
    bus = EventBus()
    sub = bus.subscribe(maxsize=10_000)
    per_thread = 250
    sources = ("worker", "sampler")

    def publisher(src: str) -> None:
        for n in range(per_thread):
            bus.publish("job_changed", {"src": src, "n": n}, at=at(n))

    got: list[Event] = []
    total = per_thread * len(sources)

    def consumer() -> None:
        deadline = time.monotonic() + 10
        while len(got) < total and time.monotonic() < deadline:
            ev = sub.get(0.5)
            if ev is not None:
                got.append(ev)

    threads = [threading.Thread(target=publisher, args=(s,)) for s in sources]
    reader = threading.Thread(target=consumer)
    reader.start()
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)
    reader.join(timeout=10)
    assert not reader.is_alive() and all(not t.is_alive() for t in threads)
    ids = [e.id for e in got]
    assert ids == list(range(1, total + 1))  # 빠짐·중복·역순 없음
    assert bus.last_id == total
    for src in sources:  # 스레드 하나가 발행한 순서는 보존된다
        seq = [e.data["n"] for e in got if e.data["src"] == src]
        assert seq == list(range(per_thread))
    bus.unsubscribe(sub)
