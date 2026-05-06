"""Tests for AudioExecutor — the deadlock-bounded PortAudio dispatcher."""

from __future__ import annotations

import threading
import time

import pytest

from thundertalk.core.audio_executor import AudioCallTimeout, AudioExecutor


def test_call_runs_on_worker_thread() -> None:
    ex = AudioExecutor()
    main_tid = threading.get_ident()
    worker_tid = ex.call(threading.get_ident)
    assert worker_tid != main_tid


def test_call_returns_result() -> None:
    ex = AudioExecutor()
    assert ex.call(lambda: 21 * 2) == 42


def test_call_propagates_exception() -> None:
    ex = AudioExecutor()

    def boom() -> None:
        raise ValueError("boom")

    with pytest.raises(ValueError, match="boom"):
        ex.call(boom)


def test_timeout_unblocks_caller() -> None:
    ex = AudioExecutor()
    t0 = time.perf_counter()
    with pytest.raises(AudioCallTimeout):
        ex.call(lambda: threading.Event().wait(), timeout=0.3)
    elapsed = time.perf_counter() - t0
    # Watchdog must release within ~timeout + small slack; never block longer.
    assert 0.25 <= elapsed <= 0.6, f"watchdog took {elapsed:.3f}s"


def test_subsequent_calls_after_normal_completion() -> None:
    ex = AudioExecutor()
    assert ex.call(lambda: "first") == "first"
    assert ex.call(lambda: "second") == "second"


def test_serializes_calls_in_order() -> None:
    """All PA work happens on one thread; calls must observe FIFO ordering."""
    ex = AudioExecutor()
    log: list[int] = []
    for i in range(10):
        ex.call(lambda i=i: log.append(i))
    assert log == list(range(10))
