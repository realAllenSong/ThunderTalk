"""Lab page — ASR file transcription + TTS synthesis."""

from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
import warnings
import wave
from pathlib import Path
from typing import Optional

import numpy as np
from PySide6.QtCore import Qt, QPointF, QRectF, QThread, Signal, QTimer
from PySide6.QtGui import QBrush, QColor, QLinearGradient, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from thundertalk.core.i18n import t
from thundertalk.core.settings import Settings
from thundertalk.ui import theme

_TTS_BASE = "http://localhost:8000"
_ACCEPTED_EXTS = frozenset({
    ".mp3", ".mp4", ".m4a", ".wav", ".flac", ".aac",
    ".mov", ".mkv", ".webm", ".aiff", ".aif", ".ogg", ".opus",
})
_DEFAULT_VOICES = ["ryan", "serena", "vivian", "aiden", "eric", "dylan", "sohee", "uncle_fu", "ono_anna"]
_DEFAULT_MODEL = "mlx-community/Qwen3-TTS-12Hz-1.7B-CustomVoice-bf16"
_DEFAULT_BASE_MODEL = "mlx-community/Qwen3-TTS-12Hz-1.7B-Base-bf16"
_LANG_OPTIONS = [
    ("Auto", ""),
    ("English", "english"),
    ("Chinese", "chinese"),
    ("Japanese", "japanese"),
    ("Korean", "korean"),
    ("French", "french"),
    ("German", "german"),
    ("Spanish", "spanish"),
    ("Italian", "italian"),
    ("Portuguese", "portuguese"),
    ("Russian", "russian"),
]


def _lbl_style(color: str) -> str:
    return f"color: {color}; background: transparent; border: none; font-size: 12px;"


# ── Utilities ─────────────────────────────────────────────────────────────────

def _find_ffmpeg() -> Optional[str]:
    p = shutil.which("ffmpeg")
    if p:
        return p
    for c in ("/opt/homebrew/bin/ffmpeg", "/usr/local/bin/ffmpeg", "/usr/bin/ffmpeg"):
        if os.path.isfile(c):
            return c
    return None


def _fmt_dur(secs: float) -> str:
    h = int(secs // 3600)
    m = int((secs % 3600) // 60)
    s = int(secs % 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def _wav_duration(data: bytes) -> float:
    try:
        import scipy.io.wavfile as wavio
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            sr, samples = wavio.read(io.BytesIO(data))
        return len(samples) / sr
    except Exception:
        pass
    try:
        with wave.open(io.BytesIO(data)) as wf:
            return wf.getnframes() / wf.getframerate()
    except Exception:
        return 0.0


def _read_wav(data: bytes) -> tuple[np.ndarray, int]:
    """Read WAV bytes; handles PCM (format 1) and IEEE_FLOAT (format 3)."""
    try:
        import scipy.io.wavfile as wavio
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            sr, samples = wavio.read(io.BytesIO(data))
        if samples.dtype != np.float32:
            if np.issubdtype(samples.dtype, np.integer):
                samples = samples.astype(np.float32) / float(np.iinfo(samples.dtype).max)
            else:
                samples = samples.astype(np.float32)
        return samples, sr
    except Exception:
        pass
    with wave.open(io.BytesIO(data)) as wf:
        sr = wf.getframerate()
        channels = wf.getnchannels()
        sw = wf.getsampwidth()
        frames = wf.readframes(wf.getnframes())
    dtype = {1: np.int8, 2: np.int16, 4: np.int32}.get(sw, np.int16)
    samples = np.frombuffer(frames, dtype=dtype).astype(np.float32)
    samples /= float(2 ** (sw * 8 - 1))
    if channels > 1:
        samples = samples.reshape(-1, channels)
    return samples, sr


def _prepare_for_playback(samples: np.ndarray, sr: int) -> tuple[np.ndarray, int]:
    """Clip and resample audio to 44100Hz for macOS CoreAudio compatibility.

    CoreAudio (PaMacCore) rejects 24kHz sample rate with err=''!obj''.
    Resampling + clipping also eliminates crackling from out-of-range float32.
    """
    import scipy.signal
    audio = np.clip(samples, -1.0, 1.0)
    target_sr = 44100
    if sr != target_sr and len(audio) > 0:
        n_out = int(round(len(audio) * target_sr / sr))
        audio = (scipy.signal.resample(audio, n_out, axis=0)
                 if audio.ndim > 1 else scipy.signal.resample(audio, n_out))
    return audio.astype(np.float32), target_sr


def _ping_tts() -> bool:
    try:
        urllib.request.urlopen(f"{_TTS_BASE}/health", timeout=1.5)
        return True
    except Exception:
        return False


def _find_tts_binary() -> Optional[str]:
    for name in ("mlx-tts", "mlx-tts-server"):
        venv_bin = Path(sys.executable).parent / name
        if venv_bin.exists():
            return str(venv_bin)
        found = shutil.which(name)
        if found:
            return found
    return None


# ── Animated bar ──────────────────────────────────────────────────────────────

class _AnimatedBar(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedHeight(5)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._active = False
        self._indeterminate = True
        self._progress = 0.0
        self._phase = 0.0
        self._timer = QTimer(self)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self._tick)

    def start_indeterminate(self) -> None:
        self._indeterminate = True
        self._progress = 0.0
        self._phase = 0.0
        self._active = True
        self._timer.start()

    def set_progress(self, pct: float) -> None:
        self._indeterminate = False
        self._progress = max(self._progress, pct / 100.0)
        if not self._active:
            self._active = True
            self._timer.start()
        self.update()

    def finish(self) -> None:
        self._progress = 1.0
        self._indeterminate = False
        self._active = False
        self._timer.stop()
        self.update()

    def reset(self) -> None:
        self._active = False
        self._indeterminate = True
        self._progress = 0.0
        self._phase = 0.0
        self._timer.stop()
        self.update()

    def _tick(self) -> None:
        if self._indeterminate:
            self._phase = (self._phase + 0.013) % 2.0
        self.update()

    def paintEvent(self, _ev) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = float(self.width()), float(self.height())
        r = h / 2
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(theme.BG_ELEVATED))
        p.drawRoundedRect(QRectF(0, 0, w, h), r, r)
        if not self._active:
            p.end()
            return
        clip = QPainterPath()
        clip.addRoundedRect(QRectF(0, 0, w, h), r, r)
        p.setClipPath(clip)
        if self._indeterminate:
            seg = w * 0.38
            pos = (self._phase if self._phase <= 1.0 else 2.0 - self._phase) * (w - seg)
            grad = QLinearGradient(pos, 0, pos + seg, 0)
            grad.setColorAt(0.0, QColor(249, 115, 22, 0))
            grad.setColorAt(0.3, QColor(249, 115, 22, 210))
            grad.setColorAt(0.7, QColor(249, 115, 22, 210))
            grad.setColorAt(1.0, QColor(249, 115, 22, 0))
            p.setBrush(QBrush(grad))
            p.drawRect(QRectF(pos, 0, seg, h))
        else:
            fill = max(self._progress * w, h)
            grad = QLinearGradient(0, 0, fill, 0)
            grad.setColorAt(0.0, QColor(249, 115, 22))
            grad.setColorAt(0.6, QColor(251, 146, 60))
            grad.setColorAt(1.0, QColor(249, 115, 22))
            p.setBrush(QBrush(grad))
            p.drawRoundedRect(QRectF(0, 0, fill, h), r, r)
        p.setClipping(False)
        p.end()


# ── Compact stacked widget (sizes to current child, not max child) ─────────────

class _CompactStack(QStackedWidget):
    def sizeHint(self):
        c = self.currentWidget()
        return c.sizeHint() if c else super().sizeHint()

    def minimumSizeHint(self):
        c = self.currentWidget()
        return c.minimumSizeHint() if c else super().minimumSizeHint()


# ── Waveform display ─────────────────────────────────────────────────────────

class _WaveformWidget(QWidget):
    clicked = Signal(float)  # position 0.0–1.0

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setFixedHeight(48)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._bars: list[float] = []
        self._pos: float = 0.0

    def load(self, samples: np.ndarray) -> None:
        mono = samples.mean(axis=1) if samples.ndim > 1 else samples
        n = len(mono)
        n_bars = 300
        if n == 0:
            self._bars = []
        else:
            chunk = max(1, n // n_bars)
            bars = [float(np.abs(mono[i * chunk: min((i + 1) * chunk, n)]).mean())
                    for i in range(n_bars)]
            mx = max(bars) if bars else 1.0
            mx = mx if mx > 1e-7 else 1.0
            self._bars = [b / mx for b in bars]
        self._pos = 0.0
        self.update()

    def set_pos(self, pos: float) -> None:
        self._pos = max(0.0, min(1.0, pos))
        self.update()

    def clear(self) -> None:
        self._bars = []
        self._pos = 0.0
        self.update()

    def mousePressEvent(self, ev) -> None:
        if ev.button() == Qt.MouseButton.LeftButton and self._bars:
            pos = float(max(0.0, min(1.0, ev.position().x() / max(1, self.width()))))
            self.set_pos(pos)
            self.clicked.emit(pos)
        super().mousePressEvent(ev)

    def paintEvent(self, _ev) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = float(self.width()), float(self.height())
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(theme.BG_ELEVATED))
        p.drawRoundedRect(QRectF(0, 0, w, h), 6, 6)

        bars = self._bars
        if not bars:
            p.setBrush(QColor(255, 255, 255, 14))
            bw, gap, cy = 2.0, 2.0, h / 2
            x = 6.0
            while x + bw < w - 6:
                p.drawRoundedRect(QRectF(x, cy - 1, bw, 2), 1, 1)
                x += bw + gap
            p.end()
            return

        n = len(bars)
        bw = (w - 4) / n
        bar_draw_w = max(0.5, bw * 0.6)
        cy, max_bh = h / 2, h - 8

        for i, amp in enumerate(bars):
            x = 2 + i * bw
            bh = max(1.5, amp * max_bh)
            played = (i / n) < self._pos
            p.setBrush(QColor(91, 141, 239, 200 if played else 50))
            p.drawRoundedRect(QRectF(x, cy - bh / 2, bar_draw_w, bh), 0.5, 0.5)

        if self._pos > 0:
            px = 2 + self._pos * (w - 4)
            p.setPen(QPen(QColor(249, 115, 22), 1.5))
            p.drawLine(QPointF(px, 3), QPointF(px, h - 3))

        p.end()


# ── ASR worker ────────────────────────────────────────────────────────────────

class _FileTranscribeWorker(QThread):
    progress = Signal(int, str)
    done = Signal(str, list)
    error = Signal(str)

    def __init__(self, engine, file_path: str) -> None:
        super().__init__()
        self._engine = engine
        self._path = file_path

    def run(self) -> None:
        try:
            ffmpeg = _find_ffmpeg()
            if not ffmpeg:
                self.error.emit("ffmpeg not found — install: brew install ffmpeg")
                return
            self.progress.emit(5, t("lab.progress.extracting"))
            cmd = [ffmpeg, "-y", "-i", self._path, "-ar", "16000", "-ac", "1", "-f", "f32le", "-"]
            r = subprocess.run(cmd, capture_output=True, timeout=600)
            if r.returncode != 0:
                raise RuntimeError(r.stderr.decode("utf-8", errors="replace")[-600:])
            samples = np.frombuffer(r.stdout, dtype=np.float32)
            if len(samples) == 0:
                self.error.emit("No audio extracted from file.")
                return
            self.progress.emit(15, t("lab.progress.segmenting"))
            from thundertalk.core.vad import segment_audio
            segs = segment_audio(samples, sr=16000)
            timed: list[tuple[float, float, str]] = []
            offset = 0.0
            total = max(len(segs), 1)
            for i, seg in enumerate(segs):
                self.progress.emit(
                    15 + int(80 * i / total),
                    t("lab.progress.transcribing").format(i=i + 1, n=total),
                )
                result = self._engine.recognize(seg, 16000)
                end = offset + len(seg) / 16000
                if result.text.strip():
                    timed.append((offset, end, result.text.strip()))
                offset = end
            self.progress.emit(100, t("lab.progress.done"))
            self.done.emit(" ".join(s[2] for s in timed), timed)
        except Exception as exc:
            import traceback; traceback.print_exc()
            self.error.emit(str(exc))


class _MossTranscribeWorker(QThread):
    """File transcription with speaker diarization via MOSS-Transcribe-Diarize.

    Unlike _FileTranscribeWorker there is no VAD segmentation — MOSS handles
    long-form audio (up to ~90 min) in a single pass and returns segments
    with timestamps and speaker labels itself.
    """

    progress = Signal(int, str)
    done = Signal(str, list)
    error = Signal(str)

    def __init__(self, file_path: str) -> None:
        super().__init__()
        self._path = file_path

    def run(self) -> None:
        import os
        import tempfile

        wav = None
        try:
            ffmpeg = _find_ffmpeg()
            if not ffmpeg:
                self.error.emit("ffmpeg not found — install: brew install ffmpeg")
                return
            self.progress.emit(5, t("lab.progress.extracting"))
            fd, wav = tempfile.mkstemp(suffix=".wav")
            os.close(fd)
            cmd = [ffmpeg, "-y", "-i", self._path, "-ar", "16000", "-ac", "1", wav]
            r = subprocess.run(cmd, capture_output=True, timeout=600)
            if r.returncode != 0:
                raise RuntimeError(r.stderr.decode("utf-8", errors="replace")[-600:])

            from thundertalk.core.diarize import load_model, merge_turns, transcribe

            self.progress.emit(20, t("lab.progress.loading_moss"))
            load_model()
            self.progress.emit(40, t("lab.progress.diarizing"))
            segs = transcribe(wav)
            if not segs:
                self.error.emit("No speech detected in file.")
                return
            timed = [
                (s.start, s.end, f"{s.speaker}: {s.text}" if s.speaker else s.text)
                for s in segs
            ]
            plain = "\n\n".join(
                f"{s.speaker}: {s.text}" if s.speaker else s.text
                for s in merge_turns(segs)
            )
            self.progress.emit(100, t("lab.progress.done"))
            self.done.emit(plain, timed)
        except Exception as exc:
            import traceback; traceback.print_exc()
            self.error.emit(str(exc))
        finally:
            if wav:
                try:
                    os.unlink(wav)
                except OSError:
                    pass


# ── TTS workers ───────────────────────────────────────────────────────────────

class _TtsServerWorker(QThread):
    """Ensure mlx-tts is running; auto-launch if offline."""
    starting = Signal()
    ready = Signal()
    error = Signal(str)

    _server_proc: Optional[subprocess.Popen] = None

    def __init__(self, model: str) -> None:
        super().__init__()
        self._model = model.strip()

    def run(self) -> None:
        if _ping_tts():
            self.ready.emit()
            return
        binary = _find_tts_binary()
        if not binary:
            self.error.emit(t("lab.tts.no_binary"))
            return
        if not self._model:
            self.error.emit("No model specified")
            return
        self.starting.emit()
        try:
            _TtsServerWorker._server_proc = subprocess.Popen(
                [binary, "serve", self._model, "--port", "8000"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception as exc:
            self.error.emit(str(exc))
            return
        for _ in range(60):
            time.sleep(0.5)
            if _ping_tts():
                self.ready.emit()
                return
        self.error.emit(t("lab.tts.server_start_timeout"))

    @classmethod
    def stop_server(cls) -> None:
        if cls._server_proc and cls._server_proc.poll() is None:
            cls._server_proc.terminate()
            cls._server_proc = None


class _TtsInstallWorker(QThread):
    """pip-install mlx-tts-server into the running venv."""
    progress = Signal(str)
    done = Signal()
    error = Signal(str)

    def run(self) -> None:
        try:
            self.progress.emit(t("lab.tts.installing"))
            uv = shutil.which("uv")
            if uv:
                cmd = [uv, "pip", "install", "mlx-tts-server"]
            else:
                cmd = [sys.executable, "-m", "pip", "install", "mlx-tts-server"]
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
            if r.returncode != 0:
                self.error.emit((r.stderr or r.stdout)[-300:] or "pip returned non-zero")
                return
            self.done.emit()
        except Exception as exc:
            self.error.emit(str(exc))


class _TtsSynthWorker(QThread):
    done = Signal(bytes, float)
    error = Signal(str)

    def __init__(self, text: str, voice: str, speed: float, instruct: str,
                 ref_path: str = "", ref_text: str = "", language: str = "") -> None:
        super().__init__()
        self._text = text
        self._voice = voice
        self._speed = speed
        self._instruct = instruct
        self._ref_path = ref_path
        self._ref_text = ref_text
        self._language = language

    def run(self) -> None:
        try:
            if self._ref_path:
                self._run_clone()
            else:
                self._run_preset()
        except Exception as exc:
            self.error.emit(str(exc))

    def _run_preset(self) -> None:
        payload: dict = {
            "model": "tts-1",
            "input": self._text,
            "voice": self._voice,
            "response_format": "wav",
            "speed": self._speed,
            "temperature": 0.9,
            "top_k": 50,
            "top_p": 1.0,
            "repetition_penalty": 1.2,
            "max_tokens": 8192,
        }
        if self._instruct.strip():
            payload["instruct"] = self._instruct.strip()
        if self._language:
            payload["language"] = self._language
        req = urllib.request.Request(
            f"{_TTS_BASE}/v1/audio/speech",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=120) as resp:
            wav = resp.read()
        self.done.emit(wav, _wav_duration(wav))

    def _run_clone(self) -> None:
        ref_path = self._ref_path
        tmp_path: Optional[str] = None
        try:
            # Extract audio track from video files before sending to clone API
            video_exts = {'.mp4', '.mov', '.mkv', '.webm'}
            if Path(ref_path).suffix.lower() in video_exts:
                ffmpeg = _find_ffmpeg()
                if ffmpeg:
                    import tempfile
                    with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as tmp:
                        tmp_path = tmp.name
                    r = subprocess.run(
                        [ffmpeg, '-y', '-i', ref_path, '-ar', '22050', '-ac', '1', '-f', 'wav', tmp_path],
                        capture_output=True, timeout=60,
                    )
                    if r.returncode == 0:
                        ref_path = tmp_path

            boundary = "----ThunderTalkClone"
            parts: list[bytes] = []

            def _field(name: str, value: str) -> bytes:
                return (
                    f"--{boundary}\r\n"
                    f'Content-Disposition: form-data; name="{name}"\r\n\r\n'
                    f"{value}\r\n"
                ).encode()

            parts.append(_field("input", self._text))
            parts.append(_field("speed", str(self._speed)))
            if self._ref_text.strip():
                parts.append(_field("ref_text", self._ref_text.strip()))
            if self._instruct.strip():
                parts.append(_field("instruct", self._instruct.strip()))

            ref_name = Path(ref_path).name
            with open(ref_path, "rb") as f:
                ref_bytes = f.read()
            parts.append((
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="ref_audio"; filename="{ref_name}"\r\n'
                "Content-Type: application/octet-stream\r\n\r\n"
            ).encode() + ref_bytes + b"\r\n")
            parts.append(f"--{boundary}--\r\n".encode())

            req = urllib.request.Request(
                f"{_TTS_BASE}/v1/audio/clone",
                data=b"".join(parts),
                headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=120) as resp:
                wav = resp.read()
            self.done.emit(wav, _wav_duration(wav))
        finally:
            if tmp_path and os.path.exists(tmp_path):
                os.unlink(tmp_path)


# ── ASR drop zone ─────────────────────────────────────────────────────────────

class _DropZone(QFrame):
    file_selected = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self.setAcceptDrops(True)
        self.setMinimumHeight(136)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._loaded_path: Optional[str] = None
        self._hovering = False

        outer = QVBoxLayout(self)
        outer.setAlignment(Qt.AlignmentFlag.AlignCenter)
        outer.setContentsMargins(28, 22, 28, 22)
        outer.setSpacing(0)

        self._idle_w = QWidget()
        self._idle_w.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        il = QVBoxLayout(self._idle_w)
        il.setAlignment(Qt.AlignmentFlag.AlignCenter)
        il.setSpacing(8)
        il.setContentsMargins(0, 0, 0, 0)
        arr = QLabel("↑")
        arr.setAlignment(Qt.AlignmentFlag.AlignCenter)
        arr.setStyleSheet(f"color: {theme.TEXT_MUTED}; background: transparent; font-size: 22px; border: none;")
        il.addWidget(arr)
        hint = QLabel(t("lab.drop.hint"))
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hint.setStyleSheet(f"color: {theme.TEXT_SECONDARY}; background: transparent; font-size: 13px; border: none;")
        il.addWidget(hint)
        il.addSpacing(4)
        fmts = QLabel("mp3  mp4  m4a  wav  flac  aac  mov  mkv  webm  ogg  opus")
        fmts.setAlignment(Qt.AlignmentFlag.AlignCenter)
        fmts.setStyleSheet(f"color: {theme.TEXT_SUBTLE}; background: transparent; font-size: 10px; letter-spacing: 1.2px; border: none;")
        il.addWidget(fmts)

        self._loaded_w = QWidget()
        self._loaded_w.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        ll = QVBoxLayout(self._loaded_w)
        ll.setAlignment(Qt.AlignmentFlag.AlignCenter)
        ll.setSpacing(5)
        ll.setContentsMargins(0, 0, 0, 0)
        self._file_icon = QLabel("🎵")
        self._file_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._file_icon.setStyleSheet("background: transparent; border: none; font-size: 26px;")
        ll.addWidget(self._file_icon)
        self._file_name = QLabel()
        self._file_name.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._file_name.setStyleSheet(f"color: {theme.TEXT_PRIMARY}; background: transparent; font-size: 13px; font-weight: bold; border: none;")
        ll.addWidget(self._file_name)
        meta = QHBoxLayout()
        meta.setAlignment(Qt.AlignmentFlag.AlignCenter)
        meta.setSpacing(6)
        self._file_dur = QLabel()
        self._file_dur.setStyleSheet(f"color: {theme.TEXT_MUTED}; background: transparent; font-size: 11px; border: none;")
        meta.addWidget(self._file_dur)
        dot = QLabel("·")
        dot.setStyleSheet(f"color: {theme.TEXT_SUBTLE}; background: transparent; font-size: 11px; border: none;")
        meta.addWidget(dot)
        chg = QLabel(t("lab.drop.change"))
        chg.setStyleSheet(f"color: {theme.ACCENT_ORANGE}; background: transparent; font-size: 11px; border: none;")
        meta.addWidget(chg)
        ll.addLayout(meta)

        self._view = QStackedWidget()
        self._view.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._view.addWidget(self._idle_w)
        self._view.addWidget(self._loaded_w)
        outer.addWidget(self._view)
        self.setStyleSheet("background: transparent;")

    def clear(self) -> None:
        self._loaded_path = None
        self._view.setCurrentIndex(0)

    def mousePressEvent(self, ev) -> None:
        if ev.button() == Qt.MouseButton.LeftButton:
            self._browse()
        super().mousePressEvent(ev)

    def _browse(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, t("lab.drop.browse"), "",
            "Audio/Video (*.mp3 *.mp4 *.m4a *.wav *.flac *.aac *.mov *.mkv *.webm *.aiff *.aif *.ogg *.opus);;All files (*)",
        )
        if path:
            self._load(path)

    def _load(self, path: str) -> None:
        self._loaded_path = path
        name = Path(path).name
        self._file_name.setText(name if len(name) <= 44 else name[:41] + "…")
        ext = Path(path).suffix.lower()
        self._file_icon.setText("🎬" if ext in {".mp4", ".mov", ".mkv", ".webm"} else "🎵")
        self._file_dur.setText("")
        ffmpeg = _find_ffmpeg()
        if ffmpeg:
            ffprobe = ffmpeg.replace("ffmpeg", "ffprobe")
            if os.path.isfile(ffprobe):
                try:
                    r = subprocess.run([ffprobe, "-v", "quiet", "-print_format", "json", "-show_format", path],
                                       capture_output=True, timeout=10)
                    self._file_dur.setText(_fmt_dur(float(json.loads(r.stdout)["format"]["duration"])))
                except Exception:
                    pass
        self._view.setCurrentIndex(1)
        self.file_selected.emit(path)

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            for url in event.mimeData().urls():
                if Path(url.toLocalFile()).suffix.lower() in _ACCEPTED_EXTS:
                    self._hovering = True
                    event.acceptProposedAction()
                    self.update()
                    return
        event.ignore()

    def dragLeaveEvent(self, event) -> None:
        self._hovering = False
        self.update()
        super().dragLeaveEvent(event)

    def dropEvent(self, event) -> None:
        self._hovering = False
        self.update()
        for url in event.mimeData().urls():
            p = url.toLocalFile()
            if Path(p).suffix.lower() in _ACCEPTED_EXTS:
                self._load(p)
                event.acceptProposedAction()
                return
        event.ignore()

    def paintEvent(self, ev) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self._hovering:
            bg, border, dashed = QColor(249, 115, 22, 20), QColor(theme.ACCENT_ORANGE), False
        elif self._loaded_path:
            bg, border, dashed = QColor(91, 141, 239, 10), QColor(91, 141, 239, 90), False
        else:
            bg, border, dashed = QColor(255, 255, 255, 4), QColor(255, 255, 255, 20), True
        pen = QPen(border, 1.5, Qt.PenStyle.DashLine if dashed else Qt.PenStyle.SolidLine)
        if dashed:
            pen.setDashPattern([4.0, 3.0])
        painter.setPen(pen)
        painter.setBrush(bg)
        painter.drawRoundedRect(QRectF(self.rect()).adjusted(1, 1, -1, -1), 8, 8)
        painter.end()
        super().paintEvent(ev)


# ── Reference audio picker (TTS clone) ────────────────────────────────────────

class _RefAudioPicker(QFrame):
    changed = Signal(str)

    _VIDEO_EXTS = frozenset({".mp4", ".mov", ".mkv", ".webm"})

    def __init__(self) -> None:
        super().__init__()
        self.setAcceptDrops(True)
        self.setMinimumHeight(90)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._path = ""
        self._hovering = False

        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 10, 16, 10)
        outer.setSpacing(0)
        outer.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # Idle state
        self._idle_w = QWidget()
        self._idle_w.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        il = QVBoxLayout(self._idle_w)
        il.setAlignment(Qt.AlignmentFlag.AlignCenter)
        il.setSpacing(4)
        il.setContentsMargins(0, 0, 0, 0)

        idle_icon = QLabel("🎤")
        idle_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        idle_icon.setStyleSheet("background: transparent; border: none; font-size: 18px;")
        il.addWidget(idle_icon)

        idle_hint = QLabel(t("lab.tts.clone_drop"))
        idle_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        idle_hint.setStyleSheet(f"color: {theme.TEXT_MUTED}; background: transparent; border: none; font-size: 12px;")
        idle_hint.setWordWrap(True)
        il.addWidget(idle_hint)

        idle_fmts = QLabel("mp3 · wav · flac · m4a · mp4 · mov · mkv")
        idle_fmts.setAlignment(Qt.AlignmentFlag.AlignCenter)
        idle_fmts.setStyleSheet(f"color: {theme.TEXT_SUBTLE}; background: transparent; border: none; font-size: 10px; letter-spacing: 0.5px;")
        il.addWidget(idle_fmts)

        # Loaded state
        self._loaded_w = QWidget()
        self._loaded_w.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        ll = QVBoxLayout(self._loaded_w)
        ll.setAlignment(Qt.AlignmentFlag.AlignCenter)
        ll.setSpacing(4)
        ll.setContentsMargins(0, 0, 0, 0)

        self._file_icon = QLabel("🎵")
        self._file_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._file_icon.setStyleSheet("background: transparent; border: none; font-size: 20px;")
        ll.addWidget(self._file_icon)

        self._file_lbl = QLabel()
        self._file_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._file_lbl.setStyleSheet(
            f"color: {theme.TEXT_PRIMARY}; background: transparent; border: none; font-size: 12px; font-weight: bold;"
        )
        ll.addWidget(self._file_lbl)

        sub = QHBoxLayout()
        sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        sub.setSpacing(12)
        chg = QLabel(t("lab.drop.change"))
        chg.setStyleSheet(f"color: {theme.ACCENT_ORANGE}; background: transparent; border: none; font-size: 11px;")
        sub.addWidget(chg)
        self._clear_btn = QPushButton("Remove")
        self._clear_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._clear_btn.setStyleSheet(
            f"QPushButton {{ background: transparent; color: {theme.TEXT_SUBTLE}; border: none; font-size: 11px; }}"
            f"QPushButton:hover {{ color: {theme.TEXT_MUTED}; }}"
        )
        self._clear_btn.clicked.connect(self._on_clear)
        sub.addWidget(self._clear_btn)
        ll.addLayout(sub)

        self._view = QStackedWidget()
        self._view.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._view.addWidget(self._idle_w)
        self._view.addWidget(self._loaded_w)
        outer.addWidget(self._view)
        self.setStyleSheet("background: transparent;")

    @property
    def path(self) -> str:
        return self._path

    def mousePressEvent(self, ev) -> None:
        if ev.button() == Qt.MouseButton.LeftButton:
            self._browse()
        super().mousePressEvent(ev)

    def _browse(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, t("lab.tts.clone_ref_label"), "",
            "Audio / Video (*.mp3 *.wav *.flac *.m4a *.aac *.ogg *.opus *.mp4 *.mov *.mkv *.webm);;All files (*)",
        )
        if path:
            self._set(path)

    def _set(self, path: str) -> None:
        self._path = path
        ext = Path(path).suffix.lower()
        self._file_icon.setText("🎬" if ext in self._VIDEO_EXTS else "🎵")
        name = Path(path).name
        self._file_lbl.setText(name if len(name) <= 48 else name[:45] + "…")
        self._view.setCurrentIndex(1)
        self.changed.emit(path)
        self.update()

    def _on_clear(self) -> None:
        self._path = ""
        self._view.setCurrentIndex(0)
        self.changed.emit("")
        self.update()

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            for url in event.mimeData().urls():
                if Path(url.toLocalFile()).suffix.lower() in _ACCEPTED_EXTS:
                    self._hovering = True
                    event.acceptProposedAction()
                    self.update()
                    return
        event.ignore()

    def dragLeaveEvent(self, event) -> None:
        self._hovering = False
        self.update()
        super().dragLeaveEvent(event)

    def dropEvent(self, event) -> None:
        self._hovering = False
        self.update()
        for url in event.mimeData().urls():
            p = url.toLocalFile()
            if Path(p).suffix.lower() in _ACCEPTED_EXTS:
                self._set(p)
                event.acceptProposedAction()
                return
        event.ignore()

    def paintEvent(self, ev) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self._hovering:
            bg, border, dashed = QColor(249, 115, 22, 20), QColor(theme.ACCENT_ORANGE), False
        elif self._path:
            bg, border, dashed = QColor(91, 141, 239, 10), QColor(91, 141, 239, 90), False
        else:
            bg, border, dashed = QColor(255, 255, 255, 3), QColor(255, 255, 255, 18), True
        pen = QPen(border, 1.5, Qt.PenStyle.DashLine if dashed else Qt.PenStyle.SolidLine)
        if dashed:
            pen.setDashPattern([4.0, 3.0])
        painter.setPen(pen)
        painter.setBrush(bg)
        painter.drawRoundedRect(QRectF(self.rect()).adjusted(1, 1, -1, -1), 8, 8)
        painter.end()
        super().paintEvent(ev)


# ── Lab page ──────────────────────────────────────────────────────────────────

class LabPage(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self._engine = None
        self._asr_worker: Optional[_FileTranscribeWorker] = None
        self._tts_server_worker: Optional[_TtsServerWorker] = None
        self._tts_install_worker: Optional[_TtsInstallWorker] = None
        self._tts_worker: Optional[_TtsSynthWorker] = None
        self._timed_segs: list[tuple[float, float, str]] = []
        self._show_timed = False
        self._tts_audio: Optional[bytes] = None
        self._tts_samples: Optional[np.ndarray] = None
        self._tts_sr: int = 24000
        self._tts_mode = 0
        self._tts_play_start: Optional[float] = None
        self._tts_dur: float = 0.0
        self._settings = Settings()

        self._real_pct: float = 0.0
        self._creep_timer = QTimer(self)
        self._creep_timer.setInterval(80)
        self._creep_timer.timeout.connect(self._creep_tick)

        self._tts_play_timer = QTimer(self)
        self._tts_play_timer.setInterval(80)
        self._tts_play_timer.timeout.connect(self._on_play_tick)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")
        root.addWidget(scroll)

        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(32, 32, 32, 32)
        layout.setSpacing(20)
        scroll.setWidget(container)

        heading_row = QHBoxLayout()
        heading_row.setSpacing(10)
        self._heading = QLabel(t("lab.title"))
        self._heading.setFont(theme.font_heading(20))
        self._heading.setStyleSheet(f"color: {theme.TEXT_PRIMARY}; background: transparent;")
        heading_row.addWidget(self._heading)
        self._exp_badge = QLabel(t("common.experimental"))
        self._exp_badge.setStyleSheet(
            f"color: {theme.ACCENT_ORANGE}; background: {theme.ACCENT_ORANGE_DIM};"
            " font-size: 10px; font-weight: 700; letter-spacing: 0.8px;"
            f" border: 1px solid {theme.ACCENT_ORANGE}55; border-radius: 8px; padding: 2px 8px;"
        )
        heading_row.addWidget(self._exp_badge)
        heading_row.addStretch()
        layout.addLayout(heading_row)

        self._subtitle = QLabel(t("lab.subtitle"))
        self._subtitle.setStyleSheet(f"color: {theme.TEXT_MUTED}; background: transparent; font-size: 13px;")
        self._subtitle.setWordWrap(True)
        layout.addWidget(self._subtitle)

        layout.addWidget(self._build_asr_card())
        layout.addWidget(self._build_tts_card())
        layout.addStretch()

        self._refresh_asr_badge()
        self._async_tts_ping()

    # ── ASR card ──────────────────────────────────────────────────────

    def _build_asr_card(self) -> QFrame:
        card = theme.make_card()
        ly = QVBoxLayout(card)
        ly.setContentsMargins(22, 18, 22, 18)
        ly.setSpacing(16)

        hdr = QHBoxLayout()
        lbl = QLabel(t("lab.asr.title"))
        lbl.setFont(theme.font(14, bold=True))
        lbl.setStyleSheet(f"color: {theme.TEXT_PRIMARY}; background: transparent; border: none;")
        hdr.addWidget(lbl)
        hdr.addStretch()
        self._asr_model_combo = QComboBox()
        self._asr_model_combo.addItem(t("lab.asr.engine_active"))
        self._asr_model_combo.addItem("MOSS-Transcribe-Diarize 0.9B")
        self._asr_model_combo.setFixedHeight(28)
        self._asr_model_combo.setStyleSheet(
            f"QComboBox {{ background: {theme.BG_ELEVATED}; color: {theme.TEXT_PRIMARY};"
            f" border: 1px solid {theme.BORDER_DEFAULT}; border-radius: 8px; padding: 0 8px; font-size: 12px; }}"
            f"QComboBox::drop-down {{ border: none; width: 18px; }}"
            f"QComboBox QAbstractItemView {{ background: {theme.BG_ELEVATED}; color: {theme.TEXT_PRIMARY};"
            f" border: 1px solid {theme.BORDER_DEFAULT}; border-radius: 6px; }}"
        )
        self._asr_model_combo.currentIndexChanged.connect(self._on_asr_model_changed)
        hdr.addWidget(self._asr_model_combo)
        self._asr_badge = QLabel()
        self._asr_badge.setStyleSheet(
            f"color: {theme.TEXT_MUTED}; background: transparent; font-size: 11px;"
            f" border: 1px solid {theme.BORDER_SUBTLE}; border-radius: 8px; padding: 2px 10px;"
        )
        hdr.addWidget(self._asr_badge)
        ly.addLayout(hdr)
        ly.addWidget(theme.separator())

        self._drop_zone = _DropZone()
        self._drop_zone.file_selected.connect(self._on_file_selected)
        ly.addWidget(self._drop_zone)

        bot = QHBoxLayout()
        bot.setSpacing(14)
        self._asr_progress_area = QWidget()
        self._asr_progress_area.setStyleSheet("background: transparent;")
        pl = QVBoxLayout(self._asr_progress_area)
        pl.setContentsMargins(0, 4, 0, 4)
        pl.setSpacing(7)
        self._anim_bar = _AnimatedBar()
        pl.addWidget(self._anim_bar)
        self._asr_progress_label = QLabel()
        self._asr_progress_label.setStyleSheet(f"color: {theme.TEXT_MUTED}; background: transparent; font-size: 11px; border: none;")
        pl.addWidget(self._asr_progress_label)
        self._asr_progress_area.setVisible(False)
        bot.addWidget(self._asr_progress_area, stretch=1)

        self._transcribe_btn = theme.accent_button(t("lab.asr.transcribe"), height=36)
        self._transcribe_btn.setFixedWidth(140)
        self._transcribe_btn.setEnabled(False)
        self._transcribe_btn.clicked.connect(self._start_asr)
        bot.addWidget(self._transcribe_btn, alignment=Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        ly.addLayout(bot)

        self._asr_output = QFrame()
        self._asr_output.setStyleSheet(
            f"QFrame {{ background: {theme.BG_BASE}; border: 1px solid {theme.BORDER_SUBTLE}; border-radius: 8px; }}"
        )
        ol = QVBoxLayout(self._asr_output)
        ol.setContentsMargins(0, 0, 0, 0)
        ol.setSpacing(0)
        oh = QHBoxLayout()
        oh.setContentsMargins(14, 10, 14, 10)
        rl = QLabel(t("lab.asr.result"))
        rl.setStyleSheet(f"color: {theme.TEXT_SECONDARY}; background: transparent; font-size: 11px; font-weight: bold; border: none;")
        oh.addWidget(rl)
        oh.addStretch()
        self._toggle_btn = QPushButton(t("lab.asr.show_timestamps"))
        self._toggle_btn.setFixedHeight(24)
        self._toggle_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._toggle_btn.setStyleSheet(
            f"QPushButton {{ background: transparent; color: {theme.TEXT_MUTED};"
            f" border: 1px solid {theme.BORDER_SUBTLE}; border-radius: 12px; font-size: 11px; padding: 0 10px; }}"
            f"QPushButton:hover {{ color: {theme.TEXT_PRIMARY}; border: 1px solid {theme.BORDER_DEFAULT}; }}"
        )
        self._toggle_btn.clicked.connect(self._toggle_view)
        oh.addWidget(self._toggle_btn)
        ol.addLayout(oh)
        ol.addWidget(theme.separator())
        _ts = (f"QTextEdit {{ background: transparent; color: {theme.TEXT_PRIMARY};"
               " border: none; padding: 14px; }} QTextEdit:focus {{ border: none; }}")
        self._plain_edit = QTextEdit()
        self._plain_edit.setReadOnly(True)
        self._plain_edit.setMinimumHeight(160)
        self._plain_edit.setFont(theme.font(14))
        self._plain_edit.setStyleSheet(_ts)
        self._timed_edit = QTextEdit()
        self._timed_edit.setReadOnly(True)
        self._timed_edit.setMinimumHeight(160)
        self._timed_edit.setFont(theme.font(13))
        self._timed_edit.setStyleSheet(_ts)
        self._text_stack = QStackedWidget()
        self._text_stack.addWidget(self._plain_edit)
        self._text_stack.addWidget(self._timed_edit)
        ol.addWidget(self._text_stack)
        ol.addWidget(theme.separator())
        acts = QHBoxLayout()
        acts.setContentsMargins(14, 8, 14, 8)
        acts.setSpacing(8)
        self._asr_copy_btn = theme.pill_button(t("lab.asr.copy"), height=28)
        self._asr_copy_btn.clicked.connect(self._asr_copy)
        acts.addWidget(self._asr_copy_btn)
        self._asr_export_btn = theme.pill_button(t("lab.asr.export"), height=28)
        self._asr_export_btn.clicked.connect(self._asr_export)
        acts.addWidget(self._asr_export_btn)
        acts.addStretch()
        self._asr_clear_btn = theme.pill_button(t("lab.asr.clear"), height=28)
        self._asr_clear_btn.clicked.connect(self._asr_clear)
        acts.addWidget(self._asr_clear_btn)
        ol.addLayout(acts)
        self._asr_output.setVisible(False)
        ly.addWidget(self._asr_output)
        return card

    # ── TTS card ──────────────────────────────────────────────────────

    def _build_tts_card(self) -> QFrame:
        card = theme.make_card()
        ly = QVBoxLayout(card)
        ly.setContentsMargins(22, 18, 22, 18)
        ly.setSpacing(14)

        # Header
        hdr = QHBoxLayout()
        lbl = QLabel(t("lab.tts.title"))
        lbl.setFont(theme.font(14, bold=True))
        lbl.setStyleSheet(f"color: {theme.TEXT_PRIMARY}; background: transparent; border: none;")
        hdr.addWidget(lbl)
        hdr.addStretch()
        self._tts_status_lbl = QLabel(t("lab.tts.offline"))
        self._tts_status_lbl.setStyleSheet(f"color: {theme.TEXT_SUBTLE}; background: transparent; border: none; font-size: 11px;")
        hdr.addWidget(self._tts_status_lbl)
        ly.addLayout(hdr)
        ly.addWidget(theme.separator())

        # Server launch panel (visible when offline)
        self._tts_server_panel = QWidget()
        self._tts_server_panel.setStyleSheet("background: transparent;")
        sp = QHBoxLayout(self._tts_server_panel)
        sp.setContentsMargins(0, 0, 0, 4)
        sp.setSpacing(8)
        srv_icon = QLabel("⚙")
        srv_icon.setStyleSheet(f"color: {theme.TEXT_SUBTLE}; background: transparent; border: none; font-size: 13px;")
        sp.addWidget(srv_icon)
        self._tts_model_edit = QLineEdit()
        saved_model = self._settings.get("tts_model") or ""
        self._tts_model_edit.setText(saved_model)
        self._tts_model_edit.setPlaceholderText(t("lab.tts.model_placeholder"))
        self._tts_model_edit.setFixedHeight(30)
        self._tts_model_edit.setStyleSheet(
            f"QLineEdit {{ background: {theme.BG_ELEVATED}; color: {theme.TEXT_PRIMARY};"
            f" border: 1px solid {theme.BORDER_DEFAULT}; border-radius: 8px; padding: 0 10px; font-size: 12px; }}"
            f"QLineEdit:focus {{ border: 1px solid rgba(249,115,22,0.4); }}"
        )
        self._tts_model_edit.textChanged.connect(lambda t_: self._settings.set("tts_model", t_.strip()))
        sp.addWidget(self._tts_model_edit, stretch=1)
        self._tts_start_btn = theme.pill_button(
            t("lab.tts.server_start"), height=30,
            bg=theme.ACCENT_ORANGE, fg="#fff",
            bg_hover=theme.ACCENT_ORANGE_HOVER, fg_hover="#fff",
            border=theme.ACCENT_ORANGE,
        )
        self._tts_start_btn.setFixedWidth(130)
        self._tts_start_btn.clicked.connect(self._launch_tts_server)
        sp.addWidget(self._tts_start_btn)
        self._tts_server_panel.setVisible(True)
        ly.addWidget(self._tts_server_panel)

        # Text input
        self._tts_input = QTextEdit()
        self._tts_input.setPlaceholderText(t("lab.tts.input_placeholder"))
        self._tts_input.setMinimumHeight(88)
        self._tts_input.setMaximumHeight(160)
        self._tts_input.setFont(theme.font(13))
        self._tts_input.setStyleSheet(
            f"QTextEdit {{ background: {theme.BG_ELEVATED}; color: {theme.TEXT_PRIMARY};"
            f" border: 1px solid {theme.BORDER_DEFAULT}; border-radius: 8px; padding: 10px; }}"
            f"QTextEdit:focus {{ border: 1px solid rgba(249,115,22,0.35); }}"
        )
        ly.addWidget(self._tts_input)

        # Mode toggle
        mode_row = QHBoxLayout()
        mode_row.setSpacing(6)
        self._tts_mode_btns: list[QPushButton] = []
        for i, key in enumerate(["lab.tts.mode_preset", "lab.tts.mode_clone"]):
            btn = QPushButton(t(key))
            btn.setFixedHeight(26)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda _, idx=i: self._set_tts_mode(idx))
            self._tts_mode_btns.append(btn)
            mode_row.addWidget(btn)
        mode_row.addStretch()
        ly.addLayout(mode_row)

        # Mode stack (compact: sizes to current child, not tallest)
        self._tts_mode_stack = _CompactStack()
        self._tts_mode_stack.setStyleSheet("background: transparent; border: none;")

        # ── Preset panel ──
        preset = QWidget()
        preset.setStyleSheet("background: transparent; border: none;")
        pla = QVBoxLayout(preset)
        pla.setContentsMargins(10, 10, 10, 10)
        pla.setSpacing(10)

        row1 = QHBoxLayout()
        row1.setSpacing(8)
        vl = QLabel(t("lab.tts.voice_label"))
        vl.setFixedWidth(42)
        vl.setStyleSheet(_lbl_style(theme.TEXT_SECONDARY))
        row1.addWidget(vl)
        self._tts_voice = QComboBox()
        for v in _DEFAULT_VOICES:
            self._tts_voice.addItem(v)
        self._tts_voice.setFixedHeight(30)
        self._tts_voice.setFixedWidth(124)
        self._tts_voice.setStyleSheet(
            f"QComboBox {{ background: {theme.BG_ELEVATED}; color: {theme.TEXT_PRIMARY};"
            f" border: 1px solid {theme.BORDER_DEFAULT}; border-radius: 8px; padding: 0 8px; font-size: 12px; }}"
            f"QComboBox::drop-down {{ border: none; width: 18px; }}"
            f"QComboBox QAbstractItemView {{ background: {theme.BG_ELEVATED}; color: {theme.TEXT_PRIMARY};"
            f" border: 1px solid {theme.BORDER_DEFAULT}; border-radius: 6px; }}"
        )
        row1.addWidget(self._tts_voice)
        row1.addSpacing(14)
        sl = QLabel(t("lab.tts.speed_label"))
        sl.setStyleSheet(_lbl_style(theme.TEXT_SECONDARY))
        row1.addWidget(sl)
        self._tts_speed = QDoubleSpinBox()
        self._tts_speed.setRange(0.25, 4.0)
        self._tts_speed.setSingleStep(0.25)
        self._tts_speed.setValue(1.0)
        self._tts_speed.setDecimals(2)
        self._tts_speed.setFixedWidth(72)
        self._tts_speed.setFixedHeight(30)
        self._tts_speed.setStyleSheet(
            f"QDoubleSpinBox {{ background: {theme.BG_ELEVATED}; color: {theme.TEXT_PRIMARY};"
            f" border: 1px solid {theme.BORDER_DEFAULT}; border-radius: 8px; padding: 0 4px; font-size: 12px; }}"
            "QDoubleSpinBox::up-button, QDoubleSpinBox::down-button { width: 0; }"
        )
        row1.addWidget(self._tts_speed)
        row1.addSpacing(14)
        ll = QLabel(t("lab.tts.lang_label"))
        ll.setStyleSheet(_lbl_style(theme.TEXT_SECONDARY))
        row1.addWidget(ll)
        self._tts_lang = QComboBox()
        for name, code in _LANG_OPTIONS:
            self._tts_lang.addItem(name, code)
        self._tts_lang.setFixedHeight(30)
        self._tts_lang.setFixedWidth(110)
        self._tts_lang.setStyleSheet(
            f"QComboBox {{ background: {theme.BG_ELEVATED}; color: {theme.TEXT_PRIMARY};"
            f" border: 1px solid {theme.BORDER_DEFAULT}; border-radius: 8px; padding: 0 8px; font-size: 12px; }}"
            f"QComboBox::drop-down {{ border: none; width: 18px; }}"
            f"QComboBox QAbstractItemView {{ background: {theme.BG_ELEVATED}; color: {theme.TEXT_PRIMARY};"
            f" border: 1px solid {theme.BORDER_DEFAULT}; border-radius: 6px; }}"
        )
        row1.addWidget(self._tts_lang)
        row1.addStretch()
        pla.addLayout(row1)

        row2 = QHBoxLayout()
        row2.setSpacing(8)
        il = QLabel(t("lab.tts.instruct_label"))
        il.setFixedWidth(42)
        il.setStyleSheet(_lbl_style(theme.TEXT_SECONDARY))
        row2.addWidget(il)
        self._tts_instruct = QLineEdit()
        self._tts_instruct.setPlaceholderText(t("lab.tts.instruct_placeholder"))
        self._tts_instruct.setFixedHeight(30)
        self._tts_instruct.setStyleSheet(
            f"QLineEdit {{ background: {theme.BG_ELEVATED}; color: {theme.TEXT_PRIMARY};"
            f" border: 1px solid {theme.BORDER_DEFAULT}; border-radius: 8px; padding: 0 10px; font-size: 12px; }}"
            f"QLineEdit:focus {{ border: 1px solid rgba(249,115,22,0.35); }}"
        )
        row2.addWidget(self._tts_instruct, stretch=1)
        pla.addLayout(row2)
        self._tts_mode_stack.addWidget(preset)

        # ── Clone panel ──
        clone = QWidget()
        clone.setStyleSheet("background: transparent; border: none;")
        cla = QVBoxLayout(clone)
        cla.setContentsMargins(0, 6, 0, 0)
        cla.setSpacing(10)

        self._ref_picker = _RefAudioPicker()
        cla.addWidget(self._ref_picker)

        crow2 = QHBoxLayout()
        crow2.setSpacing(8)
        tr_lbl = QLabel(t("lab.tts.clone_transcript_label"))
        tr_lbl.setFixedWidth(72)
        tr_lbl.setStyleSheet(_lbl_style(theme.TEXT_SECONDARY))
        crow2.addWidget(tr_lbl)
        self._clone_transcript = QLineEdit()
        self._clone_transcript.setPlaceholderText(t("lab.tts.clone_transcript_placeholder"))
        self._clone_transcript.setFixedHeight(30)
        self._clone_transcript.setStyleSheet(
            f"QLineEdit {{ background: {theme.BG_ELEVATED}; color: {theme.TEXT_PRIMARY};"
            f" border: 1px solid {theme.BORDER_DEFAULT}; border-radius: 8px; padding: 0 10px; font-size: 12px; }}"
            f"QLineEdit:focus {{ border: 1px solid rgba(249,115,22,0.35); }}"
        )
        crow2.addWidget(self._clone_transcript, stretch=1)
        cla.addLayout(crow2)

        crow3 = QHBoxLayout()
        crow3.setSpacing(8)
        cs_lbl = QLabel(t("lab.tts.speed_label"))
        cs_lbl.setFixedWidth(72)
        cs_lbl.setStyleSheet(_lbl_style(theme.TEXT_SECONDARY))
        crow3.addWidget(cs_lbl)
        self._clone_speed = QDoubleSpinBox()
        self._clone_speed.setDecimals(2)
        self._clone_speed.setRange(0.25, 4.0)
        self._clone_speed.setSingleStep(0.25)
        self._clone_speed.setValue(1.0)
        self._clone_speed.setFixedWidth(72)
        self._clone_speed.setFixedHeight(30)
        self._clone_speed.setStyleSheet(self._tts_speed.styleSheet())
        crow3.addWidget(self._clone_speed)
        clone_note = QLabel(t("lab.tts.clone_note"))
        clone_note.setStyleSheet(f"color: {theme.TEXT_SUBTLE}; background: transparent; border: none; font-size: 10px;")
        crow3.addSpacing(10)
        crow3.addWidget(clone_note)
        crow3.addStretch()
        cla.addLayout(crow3)
        self._tts_mode_stack.addWidget(clone)

        ly.addWidget(self._tts_mode_stack)

        # Animated bar for install / server-start / generate
        self._tts_anim_bar = _AnimatedBar()
        self._tts_anim_bar.setVisible(False)
        ly.addWidget(self._tts_anim_bar)

        # Progress + Generate
        bot = QHBoxLayout()
        bot.setSpacing(10)
        self._tts_progress_lbl = QLabel()
        self._tts_progress_lbl.setStyleSheet(f"color: {theme.TEXT_MUTED}; background: transparent; border: none; font-size: 11px;")
        self._tts_progress_lbl.setVisible(False)
        bot.addWidget(self._tts_progress_lbl, stretch=1)
        self._tts_gen_btn = theme.accent_button(t("lab.tts.generate"), height=36)
        self._tts_gen_btn.setFixedWidth(130)
        self._tts_gen_btn.clicked.connect(self._start_tts)
        bot.addWidget(self._tts_gen_btn)
        ly.addLayout(bot)

        # Player (waveform + controls)
        self._tts_player = QWidget()
        self._tts_player.setStyleSheet("background: transparent;")
        plv = QVBoxLayout(self._tts_player)
        plv.setContentsMargins(0, 4, 0, 0)
        plv.setSpacing(6)

        self._tts_waveform = _WaveformWidget()
        self._tts_waveform.clicked.connect(self._on_waveform_seek)
        plv.addWidget(self._tts_waveform)

        pl = QHBoxLayout()
        pl.setContentsMargins(0, 0, 0, 0)
        pl.setSpacing(8)
        self._tts_dur_lbl = QLabel()
        self._tts_dur_lbl.setStyleSheet(f"color: {theme.TEXT_MUTED}; background: transparent; border: none; font-size: 12px;")
        pl.addWidget(self._tts_dur_lbl)
        self._tts_play_btn = theme.pill_button(t("lab.tts.play"), height=30)
        self._tts_play_btn.clicked.connect(self._play_tts)
        pl.addWidget(self._tts_play_btn)
        self._tts_stop_btn = theme.pill_button(t("lab.tts.stop_play"), height=30)
        self._tts_stop_btn.clicked.connect(self._stop_tts)
        self._tts_stop_btn.setVisible(False)
        pl.addWidget(self._tts_stop_btn)
        self._tts_save_btn = theme.pill_button(t("lab.tts.save_audio"), height=30)
        self._tts_save_btn.clicked.connect(self._save_tts)
        pl.addWidget(self._tts_save_btn)
        pl.addStretch()
        plv.addLayout(pl)

        self._tts_player.setVisible(False)
        ly.addWidget(self._tts_player)

        self._set_tts_mode(0)
        return card

    # ── public API ────────────────────────────────────────────────────

    def set_engine(self, engine) -> None:
        self._engine = engine
        self._refresh_asr_badge()
        self._refresh_asr_btn()

    # ── Qt events ─────────────────────────────────────────────────────

    def showEvent(self, ev) -> None:
        super().showEvent(ev)
        self._refresh_asr_badge()
        self._refresh_asr_btn()
        self._async_tts_ping()

    def retranslate(self) -> None:
        self._heading.setText(t("lab.title"))
        self._exp_badge.setText(t("common.experimental"))
        self._subtitle.setText(t("lab.subtitle"))
        self._asr_model_combo.setItemText(0, t("lab.asr.engine_active"))
        self._refresh_asr_badge()

    # ── ASR helpers ───────────────────────────────────────────────────

    @property
    def _moss_selected(self) -> bool:
        return self._asr_model_combo.currentIndex() == 1

    def _on_asr_model_changed(self, _: int) -> None:
        self._refresh_asr_badge()
        self._refresh_asr_btn()

    def _refresh_asr_badge(self) -> None:
        if self._moss_selected:
            from thundertalk.core.models import is_downloaded
            ready = is_downloaded("moss-transcribe-diarize-mlx")
            self._asr_badge.setText(
                t("lab.asr.diarize_ready") if ready else t("lab.asr.diarize_lazy")
            )
            self._asr_badge.setStyleSheet(
                f"color: {theme.ACCENT_ORANGE}; background: transparent; font-size: 11px;"
                f" border: 1px solid {theme.ACCENT_ORANGE}55; border-radius: 8px; padding: 2px 10px;"
            )
        elif self._engine is None or not self._engine.is_loaded:
            self._asr_badge.setText(t("lab.asr.no_model"))
            self._asr_badge.setStyleSheet(
                f"color: {theme.TEXT_MUTED}; background: transparent; font-size: 11px;"
                f" border: 1px solid {theme.BORDER_SUBTLE}; border-radius: 8px; padding: 2px 10px;"
            )
        else:
            self._asr_badge.setText(f"✓ {self._engine.current_model or 'model'}")
            self._asr_badge.setStyleSheet(
                f"color: {theme.SUCCESS}; background: transparent; font-size: 11px;"
                f" border: 1px solid {theme.SUCCESS}44; border-radius: 8px; padding: 2px 10px;"
            )

    def _refresh_asr_btn(self) -> None:
        has_file = self._drop_zone._loaded_path is not None
        has_model = self._moss_selected or (
            self._engine is not None and self._engine.is_loaded
        )
        self._transcribe_btn.setEnabled(has_file and has_model and self._asr_worker is None)

    def _on_file_selected(self, _: str) -> None:
        self._refresh_asr_btn()

    def _start_asr(self) -> None:
        path = self._drop_zone._loaded_path
        if not path:
            return
        if not self._moss_selected and not self._engine:
            return
        self._transcribe_btn.setEnabled(False)
        self._real_pct = 0.0
        self._anim_bar.reset()
        self._anim_bar.start_indeterminate()
        self._asr_progress_label.setText(t("lab.progress.extracting"))
        self._asr_progress_label.setStyleSheet(f"color: {theme.TEXT_MUTED}; background: transparent; font-size: 11px; border: none;")
        self._asr_progress_area.setVisible(True)
        self._asr_output.setVisible(False)
        if self._moss_selected:
            self._asr_worker = _MossTranscribeWorker(path)
        else:
            self._asr_worker = _FileTranscribeWorker(self._engine, path)
        self._asr_worker.progress.connect(self._on_asr_progress)
        self._asr_worker.done.connect(self._on_asr_done)
        self._asr_worker.error.connect(self._on_asr_error)
        self._asr_worker.finished.connect(self._on_asr_finished)
        self._asr_worker.start()

    def _on_asr_progress(self, pct: int, msg: str) -> None:
        self._asr_progress_label.setText(msg)
        self._real_pct = float(pct)
        if pct <= 15:
            if not self._anim_bar._indeterminate:
                self._anim_bar.start_indeterminate()
        else:
            if self._anim_bar._indeterminate:
                self._anim_bar.set_progress(float(pct))
                self._creep_timer.start()
            else:
                self._anim_bar.set_progress(float(pct))

    def _creep_tick(self) -> None:
        ceiling = min(self._real_pct + 18.0, 99.0)
        cur = self._anim_bar._progress * 100.0
        if cur < ceiling:
            self._anim_bar.set_progress(min(cur + 0.3, ceiling))

    def _on_asr_done(self, plain: str, timed: list) -> None:
        self._creep_timer.stop()
        self._anim_bar.finish()
        self._timed_segs = timed
        self._plain_edit.setPlainText(plain)
        parts = []
        for s, e, tx in timed:
            ts = f"{_fmt_dur(s)} → {_fmt_dur(e)}"
            parts.append(f'<p style="margin:0 0 10px 0;"><span style="color:{theme.TEXT_MUTED};font-size:11px;">{ts}</span>&nbsp;&nbsp;{tx}</p>')
        self._timed_edit.setHtml("".join(parts))
        self._asr_progress_area.setVisible(False)
        self._asr_output.setVisible(True)

    def _on_asr_error(self, msg: str) -> None:
        self._creep_timer.stop()
        self._anim_bar.reset()
        self._asr_progress_label.setText(f"Error: {msg[:140]}")
        self._asr_progress_label.setStyleSheet(f"color: {theme.ERROR}; background: transparent; font-size: 11px; border: none;")

    def _on_asr_finished(self) -> None:
        self._asr_worker = None
        self._refresh_asr_btn()

    def _toggle_view(self) -> None:
        self._show_timed = not self._show_timed
        self._text_stack.setCurrentIndex(1 if self._show_timed else 0)
        self._toggle_btn.setText(t("lab.asr.show_plain") if self._show_timed else t("lab.asr.show_timestamps"))

    def _asr_copy(self) -> None:
        text = self._plain_edit.toPlainText()
        if text:
            QApplication.clipboard().setText(text)
            self._asr_copy_btn.setText(t("lab.asr.copied"))
            QTimer.singleShot(1500, lambda: self._asr_copy_btn.setText(t("lab.asr.copy")))

    def _asr_export(self) -> None:
        text = self._plain_edit.toPlainText()
        if not text:
            return
        path, _ = QFileDialog.getSaveFileName(self, t("lab.asr.export"), "transcription.txt", "Text files (*.txt);;All files (*)")
        if path:
            Path(path).write_text(text, encoding="utf-8")

    def _asr_clear(self) -> None:
        self._plain_edit.clear()
        self._timed_edit.clear()
        self._timed_segs = []
        self._asr_output.setVisible(False)
        self._show_timed = False
        self._text_stack.setCurrentIndex(0)
        self._toggle_btn.setText(t("lab.asr.show_timestamps"))
        self._drop_zone.clear()
        self._refresh_asr_btn()

    # ── TTS helpers ───────────────────────────────────────────────────

    def _set_tts_mode(self, idx: int) -> None:
        self._tts_mode = idx
        self._tts_mode_stack.setCurrentIndex(idx)
        active_qss = (
            f"QPushButton {{ background: {theme.BG_ELEVATED}; color: {theme.TEXT_PRIMARY};"
            f" border: 1px solid {theme.BORDER_DEFAULT}; border-radius: 13px;"
            " padding: 0 14px; font-size: 12px; font-weight: bold; }}"
        )
        inactive_qss = (
            f"QPushButton {{ background: transparent; color: {theme.TEXT_MUTED};"
            f" border: 1px solid transparent; border-radius: 13px; padding: 0 14px; font-size: 12px; }}"
            f"QPushButton:hover {{ color: {theme.TEXT_PRIMARY}; }}"
        )
        for i, btn in enumerate(self._tts_mode_btns):
            btn.setStyleSheet(active_qss if i == idx else inactive_qss)
        self._tts_mode_stack.updateGeometry()
        # Auto-suggest appropriate model for each mode
        current = self._tts_model_edit.text().strip()
        if idx == 1 and (not current or "CustomVoice" in current):
            self._tts_model_edit.setText(_DEFAULT_BASE_MODEL)
        elif idx == 0 and current == _DEFAULT_BASE_MODEL:
            self._tts_model_edit.setText(_DEFAULT_MODEL)

    def _async_tts_ping(self) -> None:
        def _check() -> None:
            online = _ping_tts()
            has_binary = _find_tts_binary() is not None
            QTimer.singleShot(0, lambda: self._set_tts_online(online, has_binary))
        threading.Thread(target=_check, daemon=True).start()

    def _set_tts_online(self, online: bool, has_binary: bool = True) -> None:
        try:
            self._tts_start_btn.clicked.disconnect()
        except Exception:
            pass
        if online:
            self._tts_status_lbl.setText(t("lab.tts.online"))
            self._tts_status_lbl.setStyleSheet(
                f"color: {theme.SUCCESS}; background: transparent; border: none; font-size: 11px;"
            )
            self._tts_server_panel.setVisible(False)
            self._tts_start_btn.setText(t("lab.tts.server_start"))
            self._tts_start_btn.clicked.connect(self._launch_tts_server)
            self._fetch_voices()
        else:
            self._tts_status_lbl.setText(t("lab.tts.offline"))
            self._tts_status_lbl.setStyleSheet(
                f"color: {theme.TEXT_SUBTLE}; background: transparent; border: none; font-size: 11px;"
            )
            self._tts_server_panel.setVisible(True)
            if has_binary:
                self._tts_start_btn.setText(t("lab.tts.server_start"))
            else:
                self._tts_start_btn.setText(t("lab.tts.install_engine"))
            self._tts_start_btn.clicked.connect(self._launch_tts_server)

    def _launch_tts_server(self) -> None:
        if not _find_tts_binary():
            self._install_tts_engine()
            return
        model = self._tts_model_edit.text().strip() or _DEFAULT_MODEL
        self._tts_model_edit.setText(model)
        self._settings.set("tts_model", model)
        self._tts_start_btn.setEnabled(False)
        self._tts_start_btn.setText(t("lab.tts.btn_starting"))
        self._tts_show_progress(t("lab.tts.server_starting"))

        self._tts_server_worker = _TtsServerWorker(model)
        self._tts_server_worker.starting.connect(lambda: self._tts_show_progress(t("lab.tts.server_starting")))
        self._tts_server_worker.ready.connect(self._on_launch_server_ready)
        self._tts_server_worker.error.connect(self._on_launch_server_error)
        self._tts_server_worker.start()

    def _install_tts_engine(self) -> None:
        self._tts_start_btn.setEnabled(False)
        self._tts_start_btn.setText(t("lab.tts.btn_installing"))
        self._tts_show_progress(t("lab.tts.installing"))
        self._tts_install_worker = _TtsInstallWorker()
        self._tts_install_worker.progress.connect(self._tts_show_progress)
        self._tts_install_worker.done.connect(self._on_install_done)
        self._tts_install_worker.error.connect(self._on_install_error)
        self._tts_install_worker.start()

    def _on_install_done(self) -> None:
        self._tts_show_progress(t("lab.tts.install_done"))
        self._tts_start_btn.setText(t("lab.tts.btn_starting"))
        self._launch_tts_server()

    def _on_install_error(self, msg: str) -> None:
        self._tts_start_btn.setEnabled(True)
        self._tts_start_btn.setText(t("lab.tts.install_engine"))
        self._tts_show_progress(f"{t('lab.tts.install_error')}: {msg[:120]}", error=True)

    def _on_launch_server_ready(self) -> None:
        self._tts_start_btn.setEnabled(True)
        self._tts_start_btn.setText(t("lab.tts.server_stop"))
        try:
            self._tts_start_btn.clicked.disconnect()
        except Exception:
            pass
        self._tts_start_btn.clicked.connect(self._stop_tts_server)
        self._set_tts_online(True)
        self._tts_hide_progress()
        self._fetch_voices()

    def _on_launch_server_error(self, msg: str) -> None:
        self._tts_start_btn.setEnabled(True)
        self._tts_start_btn.setText(t("lab.tts.server_start"))
        self._tts_show_progress(f"Error: {msg[:100]}", error=True)

    def _stop_tts_server(self) -> None:
        _TtsServerWorker.stop_server()
        self._tts_start_btn.setText(t("lab.tts.server_start"))
        try:
            self._tts_start_btn.clicked.disconnect()
        except Exception:
            pass
        self._tts_start_btn.clicked.connect(self._launch_tts_server)
        self._set_tts_online(False)

    def _fetch_voices(self) -> None:
        def _do() -> None:
            try:
                with urllib.request.urlopen(f"{_TTS_BASE}/v1/audio/speech/voices", timeout=3) as r:
                    data = json.loads(r.read())
                    voices = data.get("voices", [])
                    if voices:
                        QTimer.singleShot(0, lambda: self._populate_voices(voices))
            except Exception:
                pass
        threading.Thread(target=_do, daemon=True).start()

    def _populate_voices(self, voices: list[str]) -> None:
        current = self._tts_voice.currentText()
        self._tts_voice.clear()
        for v in voices:
            self._tts_voice.addItem(v)
        idx = self._tts_voice.findText(current)
        if idx >= 0:
            self._tts_voice.setCurrentIndex(idx)

    def _start_tts(self) -> None:
        text = self._tts_input.toPlainText().strip()
        if not text:
            return
        if self._tts_mode == 1 and not self._ref_picker.path:
            self._tts_show_progress("Select a reference audio file first", error=True)
            return
        self._tts_gen_btn.setEnabled(False)
        self._tts_player.setVisible(False)
        self._tts_audio = None

        if _ping_tts():
            self._do_tts_synth()
        elif not _find_tts_binary():
            # Not installed at all — install then auto-start then synth
            self._tts_show_progress(t("lab.tts.installing"))
            self._tts_install_worker = _TtsInstallWorker()
            self._tts_install_worker.progress.connect(self._tts_show_progress)
            self._tts_install_worker.done.connect(self._on_auto_install_done)
            self._tts_install_worker.error.connect(self._on_tts_gen_error)
            self._tts_install_worker.start()
        else:
            model = self._tts_model_edit.text().strip() or _DEFAULT_MODEL
            self._tts_show_progress(t("lab.tts.server_starting"))
            self._tts_server_worker = _TtsServerWorker(model)
            self._tts_server_worker.starting.connect(lambda: self._tts_show_progress(t("lab.tts.server_starting")))
            self._tts_server_worker.ready.connect(self._on_auto_server_ready)
            self._tts_server_worker.error.connect(self._on_tts_gen_error)
            self._tts_server_worker.start()

    def _on_auto_install_done(self) -> None:
        self._tts_show_progress(t("lab.tts.install_done"))
        model = self._tts_model_edit.text().strip() or _DEFAULT_MODEL
        self._tts_server_worker = _TtsServerWorker(model)
        self._tts_server_worker.starting.connect(lambda: self._tts_show_progress(t("lab.tts.server_starting")))
        self._tts_server_worker.ready.connect(self._on_auto_server_ready)
        self._tts_server_worker.error.connect(self._on_tts_gen_error)
        self._tts_server_worker.start()

    def _on_auto_server_ready(self) -> None:
        self._set_tts_online(True)
        self._do_tts_synth()

    def _do_tts_synth(self) -> None:
        self._tts_show_progress(t("lab.tts.generating"))
        lang = self._tts_lang.currentData() or ""
        if self._tts_mode == 0:
            self._tts_worker = _TtsSynthWorker(
                text=self._tts_input.toPlainText().strip(),
                voice=self._tts_voice.currentText(),
                speed=self._tts_speed.value(),
                instruct=self._tts_instruct.text(),
                language=lang,
            )
        else:
            self._tts_worker = _TtsSynthWorker(
                text=self._tts_input.toPlainText().strip(),
                voice="",
                speed=self._clone_speed.value(),
                instruct="",
                ref_path=self._ref_picker.path,
                ref_text=self._clone_transcript.text(),
                language=lang,
            )
        self._tts_worker.done.connect(self._on_tts_done)
        self._tts_worker.error.connect(self._on_tts_gen_error)
        self._tts_worker.finished.connect(self._on_tts_finished)
        self._tts_worker.start()

    def _on_tts_done(self, wav: bytes, dur: float) -> None:
        self._tts_audio = wav
        self._tts_dur = dur
        self._tts_hide_progress()
        self._tts_dur_lbl.setText(f"{dur:.1f}s" if dur > 0 else "")
        try:
            samples, sr = _read_wav(wav)
            self._tts_samples = samples
            self._tts_sr = sr
            self._tts_waveform.load(samples)
        except Exception:
            self._tts_samples = None
            self._tts_waveform.clear()
        self._tts_player.setVisible(True)

    def _on_tts_gen_error(self, msg: str) -> None:
        self._tts_show_progress(f"Error: {msg[:120]}", error=True)

    def _on_tts_finished(self) -> None:
        self._tts_worker = None
        self._tts_gen_btn.setEnabled(True)

    def _tts_show_progress(self, msg: str, error: bool = False) -> None:
        color = theme.ERROR if error else theme.TEXT_MUTED
        self._tts_progress_lbl.setStyleSheet(
            f"color: {color}; background: transparent; border: none; font-size: 11px;"
        )
        self._tts_progress_lbl.setText(msg)
        self._tts_progress_lbl.setVisible(True)
        if error:
            self._tts_anim_bar.reset()
            self._tts_anim_bar.setVisible(False)
        else:
            self._tts_anim_bar.start_indeterminate()
            self._tts_anim_bar.setVisible(True)

    def _tts_hide_progress(self) -> None:
        self._tts_progress_lbl.setVisible(False)
        self._tts_anim_bar.reset()
        self._tts_anim_bar.setVisible(False)

    def _play_tts(self) -> None:
        if not self._tts_audio:
            return
        self._tts_play_btn.setVisible(False)
        self._tts_stop_btn.setVisible(True)
        self._tts_waveform.set_pos(0.0)
        audio_bytes = self._tts_audio
        self._tts_play_start = time.monotonic()
        self._tts_play_timer.start()

        def _play() -> None:
            import sounddevice as sd
            try:
                audio, sr = _read_wav(audio_bytes)
                audio, sr = _prepare_for_playback(audio, sr)
                sd.play(audio, sr)
                sd.wait()
            except Exception as e:
                print(f"[TTS] Playback error: {e}")
            finally:
                QTimer.singleShot(0, self._on_play_done)

        threading.Thread(target=_play, daemon=True).start()

    def _on_play_tick(self) -> None:
        if self._tts_play_start is None or self._tts_dur <= 0:
            return
        elapsed = time.monotonic() - self._tts_play_start
        pos = min(1.0, elapsed / self._tts_dur)
        self._tts_waveform.set_pos(pos)
        if pos >= 1.0:
            self._tts_play_timer.stop()

    def _stop_tts(self) -> None:
        import sounddevice as sd
        sd.stop()
        self._on_play_done()

    def _on_play_done(self) -> None:
        self._tts_play_timer.stop()
        self._tts_play_start = None
        self._tts_stop_btn.setVisible(False)
        self._tts_play_btn.setVisible(True)

    def _on_waveform_seek(self, pos: float) -> None:
        """Seek to waveform click position and (re)start playback from there."""
        import sounddevice as sd
        if self._tts_samples is None:
            return
        sd.stop()
        self._tts_play_timer.stop()
        self._tts_waveform.set_pos(pos)
        offset = int(pos * len(self._tts_samples))
        samples_from = self._tts_samples[offset:]
        sr = self._tts_sr
        self._tts_play_start = time.monotonic() - pos * self._tts_dur
        self._tts_play_btn.setVisible(False)
        self._tts_stop_btn.setVisible(True)

        def _play() -> None:
            try:
                audio, play_sr = _prepare_for_playback(samples_from, sr)
                sd.play(audio, play_sr)
                sd.wait()
            except Exception as e:
                print(f"[TTS] Seek playback error: {e}")
            finally:
                QTimer.singleShot(0, self._on_play_done)

        threading.Thread(target=_play, daemon=True).start()
        self._tts_play_timer.start()

    def _save_tts(self) -> None:
        if not self._tts_audio:
            return
        path, _ = QFileDialog.getSaveFileName(self, t("lab.tts.save_audio"), "speech.wav", "WAV files (*.wav);;All files (*)")
        if path:
            Path(path).write_bytes(self._tts_audio)
