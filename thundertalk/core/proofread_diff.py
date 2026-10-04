"""Lossless proofreading diff: CJK characters, Latin words and punctuation."""
from dataclasses import dataclass
from difflib import SequenceMatcher
import re

_TOKEN = re.compile(r"[\u3400-\u9fff\U00020000-\U000323af]|[^\W_]+(?:['’][^\W_]+)*|\s+|.", re.UNICODE)


@dataclass(frozen=True)
class DiffSpan:
    kind: str
    original: str
    corrected: str


def proofread_diff(original: str, corrected: str) -> list[DiffSpan]:
    before, after = _TOKEN.findall(original), _TOKEN.findall(corrected)
    # Split CJK runs that the Unicode word branch can absorb after Latin text.
    def split(tokens):
        return [part for token in tokens for part in re.findall(
            r"[\u3400-\u9fff\U00020000-\U000323af]|[^\u3400-\u9fff\U00020000-\U000323af]+", token)]
    before, after = split(before), split(after)
    return [DiffSpan(kind, "".join(before[a:b]), "".join(after[c:d]))
            for kind, a, b, c, d in SequenceMatcher(None, before, after, autojunk=False).get_opcodes()]
