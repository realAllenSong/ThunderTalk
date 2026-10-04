"""Render deterministic proofreading frames, without model calls or timer waits.

QT_QPA_PLATFORM=offscreen PYTHONPATH=$PWD $PY tools/render_proofread_overlay.py [output-dir]
"""
import os
import json
from pathlib import Path
import sys

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtWidgets import QApplication  # noqa: E402

from thundertalk.ui import theme  # noqa: E402
from thundertalk.ui.overlay import VoiceOverlay  # noqa: E402
from thundertalk.core import i18n  # noqa: E402

app = QApplication(sys.argv[:1])
theme.force_light(app)
root = Path(sys.argv[1] if len(sys.argv) > 1 else '.proofread-verification/frames')
root.mkdir(parents=True, exist_ok=True)
samples = [
    ('mixed', '我们用 lama index 加上 rag 做检索', '我们用 LlamaIndex 加上 RAG 做检索'),
    ('zh', '明天再三楼开会。', '明天在三楼开会。'),
    ('en', 'the model uses g r p o', 'the model uses GRPO'),
    ('long',
     '我们用 lama index 加上 rag 做检索。明天再三楼开会，讨论新的模形。'
     '接下来检查 promp 的效果，把结果记路下来，再用 pie torch 跑一次。'
     '最后把资料传到 git hub，并且检察所有输出。然后运行 dock er，'
     '重新启动 fast api，更新 num pie，再确认 type script 的版本。',
     '我们用 LlamaIndex 加上 RAG 做检索。明天在三楼开会，讨论新的模型。'
     '接下来检查 prompt 的效果，把结果记录下来，再用 PyTorch 跑一次。'
     '最后把资料传到 GitHub，并且检查所有输出。然后运行 Docker，'
     '重新启动 FastAPI，更新 NumPy，再确认 TypeScript 的版本。'),
    ('oversized', '错' * 240, '对' * 240),
]
ov = VoiceOverlay()
frames, measurements = [], {}
def save(name):
    path = root / f'{name}.png'
    if not ov.grab().save(str(path)):
        raise RuntimeError(f'Failed to save {path}')
    frames.append(str(path.resolve()))

for name, before, after in samples:
    i18n.LANG = 'en'
    ov.show_cleanup(before)
    save(f'{name}-working')
    ov.show_cleanup_diff(before, after)
    pages = list(ov._diff_pages)
    measurements[name] = dict(pages=len(pages), durations=[p.duration for p in pages],
                              total=sum(p.duration for p in pages),
                              shown=sum(p.changes for p in pages), omitted=pages[-1].omitted)
    elapsed = 0.0
    for index, page in enumerate(pages, 1):
        for offset, suffix in ((0.0, 'transition'), (0.2, 'readable')):
            ov.advance_cleanup_animation(elapsed + offset)
            save(f'{name}-page-{index}-{suffix}')
        elapsed += page.duration
    if name == 'long':
        i18n.LANG = 'zh'
        ov.show_cleanup_diff(before, after)
        ov.advance_cleanup_animation(elapsed - pages[-1].duration + 0.2)
        save('long-last-page-zh')
ov.show_cleanup_diff('明天下午三点开会。', '明天下午三点开会。')
save('no-changes')
ov.hide_overlay()
(root / 'measurements.json').write_text(json.dumps(measurements, indent=2), encoding='utf-8')
print(f'Rendered {len(frames)} frames to {root.resolve()}')
print(json.dumps(measurements, indent=2))
