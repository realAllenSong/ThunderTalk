"""Remove spoken fillers and stutters from dictated text.

Measured on real dictations (``thundertalk eval``, 2026-11-11): the local
Qwen3-ASR transcript recognised words as well as OpenAI whisper-1, sometimes
better. Most of the remaining gap was style: whisper-1 drops fillers ("呃",
"um") and repeated restarts ("你可以你可以你可以"). This does the same,
deterministically and conservatively:

* fillers: 呃 / 嗯 / 额 and um / uh / erm only where they stand alone (between
  punctuation, spaces or the ends), and 啊 only when it is a whole clause by
  itself ("，啊，"), so "好啊" and "嗯嗯好的" stay;
* restarts: the same phrase said three or more times in a row (or a phrase of
  four or more characters said twice) is kept once. Reduplication such as
  "看看", "谢谢", "试试" is untouched.

Dictation only; Studio transcripts stay verbatim.
"""

from __future__ import annotations

import re

_P = r"[\s,，。.!！?？、;；:：]"
# A standalone filler: start/punct/space before, punct/space/end after.
_ZH_FILLER = re.compile(rf"(^|{_P})[呃额]+(?={_P}|$)|(^|{_P})嗯(?={_P}|$)")
_ZH_FILLER_GLUED = re.compile(r"(?<=[一-鿿])呃(?=[一-鿿])|^呃(?=[一-鿿])")
_A_CLAUSE = re.compile(rf"(^|[,，。!！?？])\s*啊\s*(?=[,，。!！?？]|$)")
_EN_FILLER = re.compile(r"(?i)(^|[\s,.!?])(?:um+|uh+|erm+)(?=[\s,.!?]|$)[,]?")
_REPEAT = re.compile(r"((?:[一-鿿]{2,3}|[A-Za-z]+ ?))(?:[,，、]?\s?\1){2,}")
_REPEAT_LONG = re.compile(r"([一-鿿]{4,8})(?:[,，、]?\s?\1)+")


def tidy(text: str) -> str:
    if not text:
        return text
    out = _ZH_FILLER.sub(lambda m: m.group(1) or m.group(2) or "", text)
    out = _ZH_FILLER_GLUED.sub("", out)
    out = _A_CLAUSE.sub(lambda m: m.group(1), out)
    out = _EN_FILLER.sub(lambda m: m.group(1), out)
    out = _REPEAT.sub(r"\1", out)
    out = _REPEAT_LONG.sub(r"\1", out)
    # Punctuation left doubled by a removal: "，，" → "，", leading "，" dropped.
    out = re.sub(r"([,，])\s*[,，]+", r"\1", out)
    out = re.sub(r"([。！？!?])\s*[,，]+", r"\1", out)
    out = re.sub(r"^\s*[,，、]\s*", "", out)
    out = re.sub(r"[ \t]{2,}", " ", out)
    out = out.strip()
    # Never leave only punctuation: a lone "嗯。" is an answer, not a filler.
    return out if re.search(r"[\w\u4e00-\u9fff]", out) else text.strip()
