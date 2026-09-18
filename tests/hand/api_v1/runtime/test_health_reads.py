"""Read optimizations must preserve exact statistics and snapshot revisions."""

from __future__ import annotations

import random
import threading
from collections import deque
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager

import pytest

from linkerbot_hand.api import HandDevice
from linkerbot_hand.api.errors import TimeoutError, ValidationError
from linkerbot_hand.api.health import LatencyMetrics
from linkerbot_hand.runtime.metrics import RuntimeMetrics

from .factories import make_hand, opened_runtime


@contextmanager
def opened_hand() -> Iterator[HandDevice]:
    hand, _state, _transport = make_hand()
    hand.open()
    try:
        yield hand
    finally:
        hand.close()


def reference_latency(window: deque[float]) -> LatencyMetrics:
    ordered = sorted(window)
    if not ordered:
        return LatencyMetrics()
    return LatencyMetrics(
        count=len(ordered),
        **{
            name: ordered[round((len(ordered) - 1) * fraction)]
            for name, fraction in (("p50_s", 0.5), ("p95_s", 0.95), ("p99_s", 0.99))
        },
    )


def test_health_latency_is_exact_after_updates_evictions_and_repeated_reads() -> None:
    with opened_hand() as hand:
        runtime = opened_runtime(hand)
        metrics = RuntimeMetrics(hand.get_health())
        runtime._metrics = metrics
        assert hand.get_health().transaction_latency == LatencyMetrics()
        window: deque[float] = deque(maxlen=2048)
        rng = random.Random(158)
        values = [rng.randrange(30) / 1000 for _ in range(6500)]
        values += [0.0] * 2200
        values += [1.0] * 2200
        retained = []
        for index, value in enumerate(values):
            metrics.transaction_completed(timestamp=float(index), duration_s=value)
            window.append(value)
            if index < 8 or index % 127 == 0 or index in (2047, 2048, len(values) - 1):
                expected = reference_latency(window)
                first = hand.get_health()
                assert first.transaction_latency == expected
                assert first.last_successful_transaction_at == index
                assert hand.get_health().transaction_latency == expected
                retained.append((first, expected))
        assert all(
            health.transaction_latency == expected for health, expected in retained
        )
        # A reused latency result must never freeze health/error counters.
        metrics.record_error(TimeoutError("response timed out"))
        metrics.sent(timestamp=12000.0)
        failed = hand.get_health()
        assert failed.consecutive_errors == 1
        assert failed.recent_error is not None
        assert failed.last_successful_send_at == 12000.0
        assert failed.transaction_latency == reference_latency(window)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf"), -1.0])
def test_invalid_latency_does_not_corrupt_the_window(value: float) -> None:
    with opened_hand() as hand:
        metrics = opened_runtime(hand)._metrics
        before = hand.get_health().transaction_latency
        with pytest.raises(ValidationError):
            metrics.transaction_completed(timestamp=1.0, duration_s=value)
        assert hand.get_health().transaction_latency == before


def test_concurrent_latency_updates_and_health_reads_preserve_statistics() -> None:
    with opened_hand() as hand:
        runtime = opened_runtime(hand)
        metrics = RuntimeMetrics(hand.get_health())
        runtime._metrics = metrics
        barrier = threading.Barrier(3)

        def writer() -> None:
            barrier.wait(timeout=3)
            for index in range(5000):
                metrics.transaction_completed(timestamp=float(index), duration_s=0.25)

        def reader() -> None:
            barrier.wait(timeout=3)
            for _ in range(500):
                health = hand.get_health()
                latency = health.transaction_latency
                assert 0 <= latency.count <= 2048
                if latency.count:
                    assert latency.p50_s == latency.p95_s == latency.p99_s == 0.25

        with ThreadPoolExecutor(max_workers=3) as pool:
            futures = [pool.submit(writer), pool.submit(reader), pool.submit(reader)]
            for future in futures:
                future.result(timeout=10)
        assert hand.get_health().transaction_latency == LatencyMetrics(
            2048, 0.25, 0.25, 0.25
        )
