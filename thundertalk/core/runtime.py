"""Pinned optional PyTorch wheels, installed without pip into a frozen app.

Transformers and SciPy stay bundled: MLX speech uses them too. Wheel source,
metadata and relative native-library layout are preserved (torch JIT needs
source). No user Python, shell activation or DYLD environment is needed.

Named components: ``torch`` (translation, IndexTTS) and ``music`` (ACE-Step's
small pure-Python dependencies), which requires ``torch``. Music only runs in
a freshly spawned worker, so installing it never needs an app restart.
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

_HERE = Path(__file__).parent
_MANIFEST = json.loads((_HERE / "runtime_manifest.json").read_text())
_MUSIC_MANIFEST = json.loads((_HERE / "runtime_music_manifest.json").read_text())
RUNTIME_ID = _MANIFEST["id"]
SIZE_MB = round(sum(w["size"] for w in _MANIFEST["wheels"]) / 1e6)
COMPONENTS = ("torch", "music")


def _manifest(component: str) -> dict:
    if component == "torch":
        return _MANIFEST
    if component == "music":
        return _MUSIC_MANIFEST
    raise ValueError(component)


def closure(component: str = "torch") -> tuple[str, ...]:
    """``component`` and everything it requires, dependencies first."""
    out: list[str] = []
    for dep in _manifest(component).get("requires", ()):
        out += [c for c in closure(dep) if c not in out]
    return (*out, component)


def root() -> Path:
    return Path.home() / ".thundertalk" / "runtime"


def path(component: str = "torch") -> Path:
    return root() / _manifest(component)["id"]


def _has_package(base: Path, wheel: dict) -> bool:
    name = wheel.get("package", wheel["name"])
    return (base / name / "__init__.py").is_file() or (base / f"{name}.py").is_file()


def _installed(component: str) -> bool:
    manifest = _manifest(component)
    base = path(component)
    try:
        return ((base / "complete.json").read_text() == json.dumps(manifest, sort_keys=True)
                and all(_has_package(base, w) for w in manifest["wheels"]))
    except OSError:
        return False


def installed(component: str = "torch") -> bool:
    return all(_installed(c) for c in closure(component))


def needed(component: str = "torch") -> bool:
    """Source checkouts keep using the developer's environment."""
    return bool(getattr(sys, "frozen", False)) and not installed(component)


def size_mb(component: str = "torch") -> int:
    """Download still missing for ``component`` and its requirements."""
    return round(sum(w["size"] for c in closure(component) if not _installed(c)
                     for w in _manifest(c)["wheels"]) / 1e6)


def activate(component: str = "torch") -> None:
    for component in closure(component):
        if _installed(component):
            entry = str(path(component))
            if entry not in sys.path:
                sys.path.insert(0, entry)
                importlib.invalidate_caches()


def require(component: str = "torch") -> None:
    if not getattr(sys, "frozen", False):
        return
    from thundertalk.core.i18n import t
    if needed(component):
        raise RuntimeError(t("runtime.required").format(size=size_mb(component)))
    # Transformers caches lazy class availability when first imported. Changing
    # that graph underneath a running MLX model is unsafe; restart once instead.
    if restart_needed(component):
        raise RuntimeError(t("runtime.restart"))
    activate(component)


def restart_needed(component: str = "torch") -> bool:
    if component != "torch":
        return False
    return bool(getattr(sys, "frozen", False) and _installed("torch")
                and "transformers" in sys.modules
                and str(path("torch")) not in sys.path)


def status(component: str = "torch") -> str:
    from thundertalk.core.i18n import t
    if restart_needed(component):
        return t("runtime.restart")
    return t("runtime.required").format(size=size_mb(component)) if needed(component) else ""


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


def install(progress=None, cancel=None, component: str = "torch") -> None:
    """Resume wheels, verify lockfile SHA-256, then publish atomically.

    Missing requirements of ``component`` are installed first. Interrupted
    downloads are retained. Incomplete extraction is never visible on
    sys.path. flock serializes installations across multiple app processes.
    Activation happens at next launch if Transformers is already imported.
    """
    from thundertalk.core.i18n import t
    if not getattr(sys, "frozen", False) or installed(component):
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
        parts = [c for c in closure(component) if not _installed(c)]
        if not parts:
            return
        total = sum(w["size"] for c in parts for w in _manifest(c)["wheels"])
        done = 0
        for part in parts:
            done = _install_one(part, progress, cancel, done, total)
        if component == "torch" and "transformers" not in sys.modules:
            activate()
        if progress:
            progress(100, t("runtime.restart") if restart_needed(component) else t("runtime.ready"))


def _install_one(component: str, progress, cancel, done: int, total: int) -> int:
    from thundertalk.core import models
    from thundertalk.core.i18n import t
    manifest = _manifest(component)
    base = root()
    cache = base / ".downloads" / manifest["id"]
    cache.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".install-", dir=base))
    try:
        for w in manifest["wheels"]:
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
        (stage / "complete.json").write_text(json.dumps(manifest, sort_keys=True))
        target = path(component)
        if target.exists():
            shutil.rmtree(target)
        stage.rename(target)
        shutil.rmtree(cache)
        return done
    finally:
        shutil.rmtree(stage, ignore_errors=True)
