from types import SimpleNamespace

import numpy as np

from tools.replay_dictation import replay


def test_replay_uses_guard_and_final_base(qapp):
    class Fake:
        def recognize(self, audio, *, preview=False):
            text = ("今天使用Luna模型。" if len(audio) < 30000 else
                    "不可信 Astra humanoid机器人humanoid机器人humanoid机器人") if preview else "今天使用露娜模型。"
            return SimpleNamespace(text=text)

    result = replay(Fake(), np.ones(40000, dtype=np.float32))
    assert result["loop_detected"]
    assert "Astra" not in result["chunked"]
    assert result["full"] == result["merged"] == "今天使用露娜模型。"
    assert result["duration"] == 2.5


def test_replay_clean_term_recovery(qapp):
    class Fake:
        def recognize(self, audio, *, preview=False):
            return SimpleNamespace(text="今天使用Luna模型。" if preview else "今天使用露娜模型。")

    result = replay(Fake(), np.ones(40000, dtype=np.float32))
    assert not result["loop_detected"]
    assert result["merged"] == "今天使用Luna模型。"
