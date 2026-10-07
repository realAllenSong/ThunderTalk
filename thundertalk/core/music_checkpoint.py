"""One-time, resumable XL F32 → bf16 conversion in a disposable CPU process."""
from __future__ import annotations

import json
import multiprocessing as mp
import os
import time
from pathlib import Path

BF16_MARKER = '.thundertalk-bf16.json'


def converted(folder: Path) -> bool:
    return (folder / BF16_MARKER).is_file()


def convert(folder: Path, progress=None, cancel=None) -> None:
    """Convert one shard at a time; replace only after verifying every tensor.

    No DiT is instantiated. A killed conversion leaves the previous shard or
    a completed bf16 shard intact; retries skip those and remove staging data.
    """
    import torch
    from safetensors import safe_open
    from safetensors.torch import save_file
    from thundertalk.core.models import DownloadCancelled

    if converted(folder):
        return
    torch.set_num_threads(2)
    shards = sorted(folder.glob('*.safetensors'))
    if not shards:
        raise ValueError('XL checkpoint has no safetensors shards')
    total_size = 0
    for i, shard in enumerate(shards):
        if cancel is not None and cancel.is_set():
            raise DownloadCancelled()
        stage = shard.with_suffix('.bf16-stage')
        stage.unlink(missing_ok=True)
        try:
            tensors = {}
            changed = False
            with safe_open(shard, framework='pt', device='cpu') as source:
                metadata = source.metadata() or {'format': 'pt'}
                for key in source.keys():
                    if cancel is not None and cancel.is_set():
                        raise DownloadCancelled()
                    tensor = source.get_tensor(key)
                    changed |= tensor.is_floating_point() and tensor.dtype != torch.bfloat16
                    tensors[key] = tensor.to(torch.bfloat16) if tensor.is_floating_point() else tensor
            total_size += sum(t.numel() * t.element_size() for t in tensors.values())
            if changed:
                save_file(tensors, stage, metadata=metadata)
                with safe_open(stage, framework='pt', device='cpu') as check:
                    if set(check.keys()) != set(tensors):
                        raise ValueError('Converted shard keys differ')
                    for key, expected in tensors.items():
                        if not torch.equal(check.get_tensor(key), expected):
                            raise ValueError(f'Converted tensor differs: {key}')
                os.replace(stage, shard)
            del tensors
        finally:
            stage.unlink(missing_ok=True)
        if progress:
            progress(int((i + 1) * 100 / len(shards)))
    index = folder / 'model.safetensors.index.json'
    if index.exists():
        data = json.loads(index.read_text())
        data.setdefault('metadata', {})['total_size'] = total_size
        stage = index.with_suffix('.tmp')
        stage.write_text(json.dumps(data, indent=2) + '\n')
        os.replace(stage, index)
    config = folder / 'config.json'
    if config.exists():
        data = json.loads(config.read_text())
        data['torch_dtype'] = 'bfloat16'
        stage = config.with_suffix('.tmp')
        stage.write_text(json.dumps(data, indent=2) + '\n')
        os.replace(stage, config)
    stage = folder / (BF16_MARKER + '.tmp')
    stage.write_text(json.dumps({'dtype': 'bfloat16', 'tensor_bytes': total_size,
                                 'shards': [s.name for s in shards]}) + '\n')
    os.replace(stage, folder / BF16_MARKER)


def _serve(connection, folder, cancel):
    from thundertalk.core import runtime
    from thundertalk.core.models import DownloadCancelled
    try:
        runtime.activate('torch')
        convert(Path(folder), lambda pct: connection.send(('progress', pct)), cancel)
        connection.send(('done',))
    except DownloadCancelled:
        connection.send(('cancelled',))
    except Exception as exc:
        connection.send(('error', f'{type(exc).__name__}: {exc}'))
    finally:
        connection.close()


def convert_process(folder: Path, progress=None, cancel=None) -> None:
    """Keep torch and its allocator out of the GUI download thread."""
    from thundertalk.core.models import DownloadCancelled
    if converted(folder):
        return
    ctx = mp.get_context('spawn')
    reader, writer = ctx.Pipe(duplex=False)
    stopping = ctx.Event()
    child = ctx.Process(target=_serve, args=(writer, str(folder), stopping), daemon=True)
    child.start()
    writer.close()
    start = time.monotonic()
    try:
        while True:
            if cancel is not None and cancel.is_set():
                stopping.set()
                raise DownloadCancelled()
            if time.monotonic() - start > 1800:
                raise RuntimeError('XL checkpoint conversion timed out')
            if reader.poll(0.1):
                try:
                    event = reader.recv()
                except EOFError as exc:
                    raise RuntimeError('XL conversion worker stopped') from exc
                if event[0] == 'progress' and progress:
                    progress(event[1])
                elif event[0] == 'done':
                    return
                elif event[0] == 'cancelled':
                    raise DownloadCancelled()
                elif event[0] == 'error':
                    raise RuntimeError(event[1])
            elif not child.is_alive():
                raise RuntimeError('XL conversion worker stopped')
    finally:
        child.join(2)
        if child.is_alive():
            child.terminate()
            child.join(5)
        if child.is_alive():
            child.kill()
            child.join()
        child.close()
        reader.close()
