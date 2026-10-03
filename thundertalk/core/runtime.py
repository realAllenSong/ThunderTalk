"""Pinned optional PyTorch wheels, installed without pip into a frozen app.

Transformers and SciPy stay bundled: MLX speech uses them too. Wheel source,
metadata and relative native-library layout are preserved (torch JIT needs
source). No user Python, shell activation or DYLD environment is needed.
"""
from __future__ import annotations

import hashlib
import importlib
import json
import platform
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path, PurePosixPath

_MANIFEST = json.loads(Path(__file__).with_name("runtime_manifest.json").read_text())
RUNTIME_ID = _MANIFEST["id"]
SIZE_MB = round(sum(w["size"] for w in _MANIFEST["wheels"]) / 1e6)


def root() -> Path:
    return Path.home() / ".thundertalk" / "runtime"


def installed() -> bool:
    path = root() / RUNTIME_ID
    try:
        return ((path / "complete.json").read_text() == json.dumps(_MANIFEST, sort_keys=True)
                and all((path / w["name"] / "__init__.py").is_file() for w in _MANIFEST["wheels"]))
    except OSError:
        return False


def needed() -> bool:
    """Source checkouts keep using the developer's environment."""
    return bool(getattr(sys, "frozen", False)) and not installed()


def activate() -> None:
    if installed():
        path = str(root() / RUNTIME_ID)
        if path not in sys.path:
            sys.path.insert(0, path)
            importlib.invalidate_caches()


def require() -> None:
    if not getattr(sys, "frozen", False):
        return
    from thundertalk.core.i18n import t
    if needed():
        raise RuntimeError(t("runtime.required").format(size=SIZE_MB))
    # Transformers caches lazy class availability when first imported. Changing
    # that graph underneath a running MLX model is unsafe; restart once instead.
    if restart_needed():
        raise RuntimeError(t("runtime.restart"))
    activate()


def restart_needed() -> bool:
    return bool(getattr(sys, "frozen", False) and installed()
                and "transformers" in sys.modules
                and str(root() / RUNTIME_ID) not in sys.path)


def status() -> str:
    from thundertalk.core.i18n import t
    if restart_needed():
        return t("runtime.restart")
    return t("runtime.required").format(size=SIZE_MB) if needed() else ""


def _check_cancel(cancel) -> None:
    from thundertalk.core.models import DownloadCancelled
    if cancel is not None and cancel.is_set():
        raise DownloadCancelled()


def _unpack(wheel: Path, target: Path, cancel) -> None:
    with zipfile.ZipFile(wheel) as z:
        for member in z.infolist():
            _check_cancel(cancel)
            parts = PurePosixPath(member.filename)
            if (parts.is_absolute() or ".." in parts.parts or "\\" in member.filename
                    or (member.external_attr >> 16) & 0o170000 == 0o120000):
                raise ValueError("Unsafe wheel path")
            # Wheel .data directories use installation schemes (SymPy ships
            # share/man here). Mirror pip --target without executing scripts.
            rel = parts
            if parts.parts and parts.parts[0].endswith(".data"):
                if len(parts.parts) < 3 and member.is_dir():
                    continue
                if len(parts.parts) < 3 or parts.parts[1] not in ("purelib", "platlib", "data", "headers"):
                    raise ValueError("Unsupported wheel installation scheme")
                rel = PurePosixPath(*parts.parts[2:])
                if parts.parts[1] == "headers":
                    rel = PurePosixPath("include") / rel
            dest = target.joinpath(*rel.parts)
            if member.is_dir():
                dest.mkdir(parents=True, exist_ok=True)
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            with z.open(member) as src, dest.open("wb") as dst:
                while chunk := src.read(1024 * 1024):
                    _check_cancel(cancel)
                    dst.write(chunk)


def install(progress=None, cancel=None) -> None:
    """Resume wheels, verify lockfile SHA-256, then publish atomically.

    Interrupted downloads are retained. Incomplete extraction is never visible
    on sys.path. flock serializes installations across multiple app processes.
    Activation happens at next launch if Transformers is already imported.
    """
    from thundertalk.core import models
    from thundertalk.core.i18n import t
    if installed() or not getattr(sys, "frozen", False):
        return
    if sys.version_info[:2] != (3, 12) or platform.system() != "Darwin" or platform.machine() != "arm64":
        raise RuntimeError(t("runtime.unsupported"))
    import fcntl
    import time
    base = root()
    base.mkdir(parents=True, exist_ok=True)
    with (base / ".install.lock").open("a") as lock:
        while True:
            _check_cancel(cancel)
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                time.sleep(0.1)
        if installed():
            return
        cache = base / ".downloads" / RUNTIME_ID
        cache.mkdir(parents=True, exist_ok=True)
        stage = Path(tempfile.mkdtemp(prefix=".install-", dir=base))
        total = sum(w["size"] for w in _MANIFEST["wheels"])
        done = 0
        try:
            for w in _MANIFEST["wheels"]:
                _check_cancel(cancel)
                wheel = cache / w["url"].rsplit("/", 1)[-1]
                lo, hi = int(done * 90 / total), int((done + w["size"]) * 90 / total)
                if wheel.exists() and wheel.stat().st_size > w["size"]:
                    wheel.unlink()
                if not wheel.exists() or wheel.stat().st_size != w["size"]:
                    models._curl_download(w["url"], wheel, progress, cancel, lo, hi,
                                          resume=True, total_bytes=w["size"])
                if progress:
                    progress(hi, t("runtime.verifying").format(name=w["name"]))
                digest = hashlib.sha256()
                with wheel.open("rb") as f:
                    while chunk := f.read(1024 * 1024):
                        _check_cancel(cancel)
                        digest.update(chunk)
                if digest.hexdigest() != w["sha256"]:
                    wheel.unlink(missing_ok=True)
                    raise ValueError(t("runtime.integrity"))
                _unpack(wheel, stage, cancel)
                done += w["size"]
            _check_cancel(cancel)
            (stage / "complete.json").write_text(json.dumps(_MANIFEST, sort_keys=True))
            target = base / RUNTIME_ID
            if target.exists():
                shutil.rmtree(target)
            stage.rename(target)
            shutil.rmtree(cache)
            if "transformers" not in sys.modules:
                activate()
            if progress:
                progress(100, t("runtime.restart") if restart_needed() else t("runtime.ready"))
        finally:
            shutil.rmtree(stage, ignore_errors=True)
