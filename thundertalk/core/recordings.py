"""Private local rolling dictation recordings. No network operations."""

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import threading
import uuid

import numpy as np

from thundertalk.core.audio_io import wav_bytes

_LOCK = threading.Lock()


def save_recording(samples: np.ndarray, *, model: str, language: str,
                   final_text: str, preview_text: str, pasted_text: str,
                   loop_detected: bool, hotwords: list[str] | None = None,
                   directory: Path | None = None) -> Path:
    """Save 16 kHz mono PCM + metadata, then retain only the latest 20 pairs.

    Metadata is the completion marker; failed writes remove their audio.
    UUID suffixes avoid collisions between rapid dictations. The app calls
    this on a worker after dispatching the paste, and catches storage errors.
    """
    directory = directory or Path.home() / ".thundertalk" / "recordings"
    now = datetime.now(timezone.utc)
    with _LOCK:
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        stem = now.strftime("%Y%m%dT%H%M%S%fZ") + "-" + uuid.uuid4().hex[:8]
        audio, sidecar = directory / (stem + ".wav"), directory / (stem + ".json")
        data = {"timestamp": now.isoformat(), "model": model, "language": language,
                "duration": len(samples) / 16000, "final_asr_text": final_text,
                "last_preview_text": preview_text, "merged_pasted_text": pasted_text,
                "loop_detected": loop_detected, "hotwords": hotwords or []}
        try:
            for path, content in ((audio, wav_bytes(samples, 16000)),
                                  (sidecar, json.dumps(data, ensure_ascii=False,
                                                      indent=2).encode("utf-8"))):
                fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, "wb") as out:
                    out.write(content)
            for old in sorted(directory.glob("*.json"))[:-20]:
                old.with_suffix(".wav").unlink(missing_ok=True)
                old.unlink(missing_ok=True)
        except Exception:
            audio.unlink(missing_ok=True)
            sidecar.unlink(missing_ok=True)
            raise
        return sidecar
