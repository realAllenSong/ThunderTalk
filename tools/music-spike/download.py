"""Fetch only model data into the isolated bench directory, never remote code."""
from fnmatch import fnmatch
import json
import os
from pathlib import Path
import shutil
import sys

ROOT = Path.home() / "Library/Caches/ThunderTalk-bench/music-models"
ALLOW = ("*.json", "*.safetensors", "*.pt", "*.txt", "*.model", "*.tiktoken", "*.jinja")


def main():
    from huggingface_hub import HfApi, hf_hub_download

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
        hf_hub_download(repo, f.rfilename, revision=info.sha, local_dir=ROOT / "checkpoints")
    if '--xl' in sys.argv:
        repo = 'ACE-Step/acestep-v15-xl-sft'
        info = api.model_info(repo, files_metadata=True)
        files = [f for f in info.siblings if any(fnmatch(f.rfilename, glob) for glob in ALLOW)]
        folder = ROOT / 'checkpoints/acestep-v15-xl-sft'
        total = sum(f.size for f in files)
        if shutil.disk_usage(ROOT).free < 1.2 * total:
            raise RuntimeError('Insufficient disk for XL download and conversion')
        (ROOT / 'download-xl-receipt.json').write_text(json.dumps(
            {'repo': repo, 'revision': info.sha, 'files': [{'path': f.rfilename, 'size': f.size} for f in files]},
            indent=2) + '\n')
        for f in files:
            print(f.rfilename, f.size, flush=True)
            hf_hub_download(repo, f.rfilename, revision=info.sha, local_dir=folder)
        # Convert one shard at a time, publish atomically, then release its FP32 file.
        import torch
        from safetensors.torch import load_file, save_file
        for path in folder.glob('*.safetensors'):
            state = load_file(path)
            if all(not value.is_floating_point() or value.dtype == torch.bfloat16 for value in state.values()):
                continue
            converted = {key: value.to(torch.bfloat16) if value.is_floating_point() else value
                         for key, value in state.items()}
            stage = path.with_suffix('.bf16-stage')
            save_file(converted, stage, metadata={'format': 'pt'})
            # Verify every tensor before discarding the original shard.
            restored = load_file(stage)
            if any(not torch.equal(restored[key], value) for key, value in converted.items()):
                raise RuntimeError(f'BF16 conversion verification failed: {path}')
            del restored, converted, state
            stage.replace(path)
        index = folder / 'model.safetensors.index.json'
        if index.exists():
            metadata = json.loads(index.read_text())
            metadata.setdefault('metadata', {})['total_size'] = sum(p.stat().st_size for p in folder.glob('*.safetensors'))
            index.write_text(json.dumps(metadata, indent=2) + '\n')
    print("Download complete", flush=True)


if __name__ == "__main__":
    # Keep HF transfer metadata and any intermediate files out of the user's cache.
    os.environ.setdefault("HF_HOME", str(ROOT / "hf-cache"))
    os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
    main()
