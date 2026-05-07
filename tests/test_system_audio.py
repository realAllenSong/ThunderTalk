"""Tests for the non-blocking system audio dispatcher."""

from __future__ import annotations

import threading
import time

from thundertalk.core import system_audio


class _FakeExecutor:
    def __init__(self) -> None:
        self.jobs: list[str] = []

    def submit(self, fn) -> None:
        self.jobs.append(fn.__name__)


def test_public_entrypoints_enqueue_worker_jobs(monkeypatch) -> None:
    fake = _FakeExecutor()
    monkeypatch.setattr(system_audio, "_get_system_audio_executor", lambda: fake)

    system_audio.mute_system_audio()
    system_audio.unmute_system_audio()
    system_audio.ensure_audio_restored()
    system_audio.force_unmute()

    assert fake.jobs == [
        "_mute_system_audio_sync",
        "_unmute_system_audio_sync",
        "_ensure_audio_restored_sync",
        "_force_unmute_sync",
    ]


def test_executor_submit_does_not_wait_for_blocked_job() -> None:
    executor = system_audio._SystemAudioExecutor()
    blocker = threading.Event()

    started = threading.Event()

    def blocked_job() -> None:
        started.set()
        blocker.wait()

    t0 = time.perf_counter()
    executor.submit(blocked_job)
    elapsed = time.perf_counter() - t0

    assert elapsed < 0.05
    assert started.wait(timeout=0.5)
    blocker.set()
    executor.shutdown()


def test_public_entrypoints_do_not_wait_for_state_lock(monkeypatch) -> None:
    fake = _FakeExecutor()
    monkeypatch.setattr(system_audio, "_get_system_audio_executor", lambda: fake)

    assert system_audio._lock.acquire(timeout=0.5)
    try:
        t0 = time.perf_counter()
        system_audio.ensure_audio_restored()
        elapsed = time.perf_counter() - t0
    finally:
        system_audio._lock.release()

    assert elapsed < 0.05
    assert fake.jobs == ["_ensure_audio_restored_sync"]
