"""Bounded audio-state diagnostics: no text, audio, device names or hardware UIDs."""
from __future__ import annotations

import json
import logging
import os
import threading
from logging.handlers import RotatingFileHandler
from pathlib import Path

_logger = None
_lock = threading.Lock()


class _PrivateRotatingFileHandler(RotatingFileHandler):
    def _open(self):
        fd = os.open(self.baseFilename, os.O_CREAT | os.O_APPEND | os.O_WRONLY, 0o600)
        os.fchmod(fd, 0o600)
        return os.fdopen(fd, "a", encoding=self.encoding)


def _get_logger():
    global _logger
    with _lock:
        if _logger is None:
            path = Path.home() / ".thundertalk" / "logs" / "audio.log"
            path.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(path, os.O_CREAT | os.O_APPEND | os.O_WRONLY, 0o600)
            os.close(fd)
            logger = logging.Logger("thundertalk.audio", level=logging.INFO)
            handler = _PrivateRotatingFileHandler(path, maxBytes=96 * 1024, backupCount=2, encoding="utf-8")
            handler.setFormatter(logging.Formatter("%(asctime)s pid=%(process)d %(message)s"))
            logger.addHandler(handler)
            logger.propagate = False
            _logger = logger
        return _logger


def audio_diagnostic(event: str, **fields) -> None:
    """Call sites pass only numeric state, static labels and exception *types*."""
    try:
        _get_logger().info(json.dumps({"event": event, **fields}, sort_keys=True))
    except Exception:
        pass  # logging/disk failures must never prevent speaker restoration
