"""Live Codex check with synthetic two-speaker transcripts; never run by tests.

Run through lock.py gpu with PYTHONPATH=$PWD and the shared interpreter.
Outputs quality-review samples and measured latency to stdout only.
"""
from __future__ import annotations

import json
import argparse
import time

from thundertalk.core import meeting_notes
from thundertalk.core.llm_providers import detect
from thundertalk.core.transcribe import Segment, Transcript

TURNS_ZH = [
    ("S01", "今天讨论客户试点。上次说要直接全面上线，这个提议暂时不采纳。"),
    ("S02", "我建议先让十个测试账号试用，但费用预算还没确认。"),
    ("S01", "同意先做十个账号的试点。请产品负责人在周五之前写好试点说明。"),
    ("S02", "好的，我是产品负责人，我会写说明。另外，测试账号名单由谁整理？"),
    ("S01", "名单的负责人还没定。试点什么时候开始也没定，等预算确认再讨论。"),
    ("S02", "我会检查登录错误，不过没说完成时间。费用预算是多少仍然需要财务回复。"),
]
TURNS_EN = [
    ("S01", "We are discussing a customer pilot. We are not accepting the earlier full launch proposal."),
    ("S02", "I suggest starting with ten test accounts. The budget is still unconfirmed."),
    ("S01", "Agreed, ten accounts first. Product lead, please write the pilot guide by Friday."),
    ("S02", "I am the product lead and will write it. Who will prepare the account list?"),
    ("S01", "The list owner and the pilot start date are not decided. We need the budget first."),
    ("S02", "I will check the login errors. There is no deadline yet. Finance still needs to confirm the budget."),
]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--sample", choices=("zh", "en", "zh-long"))
    args = parser.parse_args()
    started = time.monotonic()
    providers = detect()
    provider = next((p for p in providers if p.id == "codex" and p.is_ready()), None)
    print(json.dumps({"detection_seconds": round(time.monotonic() - started, 3),
                      "codex_ready": provider is not None}), flush=True)
    if provider is None:
        raise RuntimeError("Codex is not ready")
    # Repetition exercises the chunk + merge path without inventing private data.
    for language, turns, repeat in (("zh", TURNS_ZH, 1), ("en", TURNS_EN, 1), ("zh-long", TURNS_ZH, 40)):
        if args.sample and language != args.sample:
            continue
        transcript = Transcript(
            [Segment(i * 10, (i + 1) * 10, text, speaker) for i, (speaker, text) in enumerate(turns * repeat)],
            len(turns) * repeat * 10, "Synthetic (no ASR)", has_speakers=True,
            speaker_names={"S01": "主持人" if language.startswith("zh") else "Facilitator",
                           "S02": "产品负责人" if language.startswith("zh") else "Product lead"})
        events = []
        started = time.monotonic()
        result = meeting_notes.generate(transcript, provider, "gpt-6.1-sol",
                                        progress=lambda p, m: events.append((p, m)))
        print(json.dumps({"sample": language, "model": "gpt-6.1-sol", "segments": len(transcript.segments),
                          "chunks": len(meeting_notes.transcript_chunks(transcript)),
                          "seconds": round(time.monotonic() - started, 3), "progress": events,
                          "notes": result}, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
