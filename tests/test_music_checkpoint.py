import json
import threading

import pytest

from thundertalk.core import music_checkpoint
from thundertalk.core.models import DownloadCancelled


def test_checkpoint_conversion_resumes_and_keeps_only_bf16(tmp_path):
    torch = pytest.importorskip('torch')
    from safetensors.torch import load_file, save_file
    # Includes large magnitudes, integers and values exactly representable in bf16.
    original = torch.tensor([0.0, 1.0, -2.0, 65536.0, 0.5])
    save_file({'weight': original, 'indices': torch.tensor([1, 2], dtype=torch.int64)}, tmp_path / 'a.safetensors')
    save_file({'weight': original * 2}, tmp_path / 'b.safetensors')
    (tmp_path / 'config.json').write_text('{}')
    (tmp_path / 'model.safetensors.index.json').write_text(json.dumps({'weight_map': {'weight': 'a.safetensors'}}))
    cancel = threading.Event()
    with pytest.raises(DownloadCancelled):
        music_checkpoint.convert(tmp_path, lambda p: cancel.set(), cancel)
    assert not music_checkpoint.converted(tmp_path)
    assert load_file(tmp_path / 'a.safetensors')['weight'].dtype == torch.bfloat16
    assert load_file(tmp_path / 'b.safetensors')['weight'].dtype == torch.float32
    music_checkpoint.convert(tmp_path)
    assert music_checkpoint.converted(tmp_path)
    restored = load_file(tmp_path / 'a.safetensors')
    assert torch.equal(restored['weight'].float(), original)
    assert restored['indices'].dtype == torch.int64
    assert json.loads((tmp_path / 'config.json').read_text())['torch_dtype'] == 'bfloat16'
    assert json.loads((tmp_path / 'model.safetensors.index.json').read_text())['metadata']['total_size'] == 36
    assert not list(tmp_path.glob('*.bf16-stage'))
    before = (tmp_path / 'a.safetensors').stat().st_mtime_ns
    music_checkpoint.convert(tmp_path)
    assert (tmp_path / 'a.safetensors').stat().st_mtime_ns == before
