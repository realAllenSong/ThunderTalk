"""Fetch only model data into the isolated bench directory, never remote code."""
from fnmatch import fnmatch
import json
import os
from pathlib import Path
import shutil
import sys

ROOT = Path.home() / "Library/Caches/ThunderTalk-bench/music-models"
ALLOW = ("*.json", "*.safetensors", "*.pt", "*.txt", "*.model", "*.tiktoken", "*.jinja")
ATTEMPTS = 8


def fetch(repo, name, revision, local_dir):
    """hf_hub_download with retries; the partial ``.incomplete`` file is resumed each time."""
    import time
    from huggingface_hub import hf_hub_download
    for attempt in range(1, ATTEMPTS + 1):
        try:
            return hf_hub_download(repo, name, revision=revision, local_dir=local_dir,
                                   etag_timeout=60)
        except Exception as e:      # noqa: BLE001 - network errors come from httpx/hf in many types
            if attempt == ATTEMPTS:
                raise
            print(f"retry {attempt}/{ATTEMPTS - 1} for {name}: {type(e).__name__}: {e}", flush=True)
            time.sleep(min(300, 15 * 2 ** attempt))


def main():
    from huggingface_hub import HfApi

    if '--convert-only' in sys.argv:
        from thundertalk.core.music_checkpoint import convert_process
        folder = ROOT / 'checkpoints/acestep-v15-xl-sft'
        if not (folder / '.thundertalk-fetched-xl').is_file():
            raise RuntimeError('XL is not fully fetched yet')
        convert_process(folder, lambda pct: print(f"XL bf16 {pct}%", flush=True))
        print("Download complete", flush=True)
        return
    ROOT.mkdir(parents=True, exist_ok=True)
    api = HfApi()
    repo = "ACE-Step/Ace-Step1.5"
    info = api.model_info(repo, files_metadata=True)
    names = {"acestep-v15-turbo", "vae", "Qwen3-Embedding-0.6B"}
    if "--planner" in sys.argv:
        names.add("acestep-5Hz-lm-1.7B")
    files = [f for f in info.siblings if f.rfilename.split('/')[0] in names
             and any(fnmatch(f.rfilename, glob) for glob in ALLOW)]
    total = sum(f.size for f in files)
    if total > 40_000_000_000 or shutil.disk_usage(ROOT).free < 1.2 * total:
        raise RuntimeError("Download exceeds the bench disk budget")
    receipt = {"repo": repo, "revision": info.sha,
               "files": [{"path": f.rfilename, "size": f.size} for f in files]}
    (ROOT / "download-receipt.json").write_text(json.dumps(receipt, indent=2) + '\n')
    print(f"Downloading {len(files)} data files, {total / 1e9:.3f} GB, revision {info.sha}", flush=True)
    for f in files:
        print(f.rfilename, f.size, flush=True)
        fetch(repo, f.rfilename, info.sha, ROOT / "checkpoints")
    from thundertalk.core.music_checkpoint import converted, convert_process
    if '--xl' in sys.argv and not converted(ROOT / 'checkpoints/acestep-v15-xl-sft'):
        repo = 'ACE-Step/acestep-v15-xl-sft'
        info = api.model_info(repo, files_metadata=True)
        files = [f for f in info.siblings if any(fnmatch(f.rfilename, glob) for glob in ALLOW)]
        folder = ROOT / 'checkpoints/acestep-v15-xl-sft'
        total = sum(f.size for f in files)
        fetched = folder / '.thundertalk-fetched-xl'
        scratch = max((p.stat().st_size for p in folder.glob('*.safetensors')), default=0) / 2
        needed = scratch if fetched.is_file() else total
        if shutil.disk_usage(ROOT).free < 1.2 * needed:
            raise RuntimeError('Insufficient disk for XL download and conversion')
        (ROOT / 'download-xl-receipt.json').write_text(json.dumps(
            {'repo': repo, 'revision': info.sha, 'files': [{'path': f.rfilename, 'size': f.size} for f in files]},
            indent=2) + '\n')
        if not fetched.is_file():
            for f in files:
                print(f.rfilename, f.size, flush=True)
                fetch(repo, f.rfilename, info.sha, folder)
            fetched.write_text(info.sha)
        if '--fetch-only' in sys.argv:
            print("Fetch complete", flush=True)
            return
        convert_process(folder, lambda pct: print(f"XL bf16 {pct}%", flush=True))
    print("Download complete", flush=True)


if __name__ == "__main__":
    # Keep HF transfer metadata and any intermediate files out of the user's cache.
    os.environ.setdefault("HF_HOME", str(ROOT / "hf-cache"))
    os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
    main()
