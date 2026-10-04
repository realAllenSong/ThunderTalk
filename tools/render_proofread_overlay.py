"""Render deterministic proofreading frames, without model calls or timer waits.

QT_QPA_PLATFORM=offscreen PYTHONPATH=$PWD $PY tools/render_proofread_overlay.py [output-dir]
"""
import os
from pathlib import Path
import sys

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtWidgets import QApplication  # noqa: E402

from thundertalk.ui import theme  # noqa: E402
from thundertalk.ui.overlay import VoiceOverlay  # noqa: E402

app = QApplication(sys.argv[:1])
theme.force_light(app)
root = Path(sys.argv[1] if len(sys.argv) > 1 else '.proofread-verification/frames')
root.mkdir(parents=True, exist_ok=True)
samples = [
    ('mixed', '我们用 lama index 加上 rag 做检索', '我们用 LlamaIndex 加上 RAG 做检索'),
    ('zh', '明天再三楼开会。', '明天在三楼开会。'),
    ('en', 'the model uses g r p o', 'the model uses GRPO'),
]
ov = VoiceOverlay()
for name, before, after in samples:
    ov.show_cleanup(before)
    ov.grab().save(str(root / f'{name}-working.png'))
    ov.show_cleanup_diff(before, after)
    for elapsed in (0.0, 0.3, 0.8, 1.2):
        ov.advance_cleanup_animation(elapsed)
        ov.grab().save(str(root / f'{name}-{elapsed:.1f}.png'))
ov.show_cleanup_diff('明天下午三点开会。', '明天下午三点开会。')
ov.grab().save(str(root / 'no-changes.png'))
ov.hide_overlay()
print(f'Rendered 16 frames to {root}')
