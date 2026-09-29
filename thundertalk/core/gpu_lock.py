"""One lock for every MLX/Metal job in the process.

Dictation (Qwen3-ASR), file transcription (MOSS) and speech synthesis all run
on the same GPU from different threads. MLX is not designed for arbitrary
concurrent evaluation of separate models from several threads, so heavy calls
take this lock. Hold it per unit of work (one utterance, one sentence) — not
for a whole long job — so a hotkey dictation never waits more than a few
seconds behind a synthesis running in the background.
"""

from __future__ import annotations

import threading

GPU_LOCK = threading.RLock()
