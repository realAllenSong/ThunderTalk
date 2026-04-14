"""Inverse Text Normalization (ITN) — convert spoken numbers to digits.

Lightweight, zero-dependency post-processor that converts Chinese and English
spoken-form numbers to Arabic digits:
    "一千"       → "1000"
    "三百五十二"  → "352"
    "两万"       → "20000"
    "twenty five" → "25"

Context-aware: does NOT convert non-numeric usages like "一下", "一些", "一起".
Only converts when the span contains unit characters (十百千万亿) or is a
multi-digit sequence (一二三 → 123).
"""

from __future__ import annotations

import re

# ── Chinese number mapping ───────────────────────────────────────────────

_ZH_DIGITS: dict[str, int] = {
    "零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3,
    "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9,
}

_ZH_UNITS: dict[str, int] = {
    "十": 10, "拾": 10,
    "百": 100, "佰": 100,
    "千": 1000, "仟": 1000,
    "万": 10_000,
    "亿": 100_000_000,
}

_ZH_UNIT_CHARS = set(_ZH_UNITS.keys())

# Common words starting with a digit char that should NOT be normalised.
# "一下", "一些", "一起", "一直", "一定", "一般", "一样", "一边",
# "一旦", "一共", "一向", "一味", "一致", "一贯", "一概", etc.
_ZH_NO_CONVERT_SUFFIXES = frozenset(
    "下些起直定般样边旦共向味致贯概度番遍切而再且又并非但"
    "来去面路带辈阵子把个只条件层片块位场双节本张道口间"
    "种类份段组套批根台架坛所门座栋间扇期集首部册卷幅"
    "堆群排列名头只匹支枚颗粒滴缕丝股串捆扎簇束团伙帮拨"
)

# Pattern to find contiguous runs of Chinese number characters
_ZH_NUM_RE = re.compile(
    r"[负負]?"
    r"[零〇一二两三四五六七八九十拾百佰千仟万亿]+"
    r"(?:点[零〇一二两三四五六七八九]+)?"
)


def _should_convert_zh(match: re.Match, original_text: str) -> bool:
    """Decide whether a matched Chinese number span should be normalised.

    Rules:
    - Always convert if the span contains a unit char (十百千万亿)
    - Always convert multi-digit sequences (≥ 2 digit chars, no units) — phone-number style
    - Always convert decimals (contains 点)
    - For single-digit matches (e.g. "一"), only convert if followed by a
      known measure-word / counter, NOT by common non-numeric suffixes.
    """
    text = match.group()
    core = text.lstrip("负負")

    # Has unit character → definitely a number (一千, 三百五十二, 两万)
    if any(c in _ZH_UNIT_CHARS for c in core):
        return True

    # Has decimal point → definitely a number (三点一四)
    if "点" in core:
        return True

    # Multi-digit sequence → phone-number style (一二三四五)
    digit_count = sum(1 for c in core if c in _ZH_DIGITS)
    if digit_count >= 2:
        return True

    # Single digit (一, 二, 三...) — check what follows
    end = match.end()
    if end < len(original_text):
        next_char = original_text[end]
        # If followed by a non-numeric suffix, don't convert
        if next_char in _ZH_NO_CONVERT_SUFFIXES:
            return False

    # Single "零" or "两" alone usually don't need conversion
    if core in ("零", "〇"):
        return False

    # Single digit at end of string or followed by punctuation — don't convert
    # (e.g. "第一" should stay as-is)
    if digit_count == 1:
        return False

    return True


def _parse_zh_integer(s: str) -> int | None:
    """Parse a Chinese integer string like '三百五十二' → 352."""
    if not s:
        return None

    # Pure digit sequence like "一二三" → 123 (phone-number style)
    if all(c in _ZH_DIGITS for c in s) and len(s) > 1 and not any(c in s for c in "十百千万亿拾佰仟"):
        return int("".join(str(_ZH_DIGITS[c]) for c in s))

    result = 0
    current = 0
    yi_part = 0    # 亿 accumulator
    wan_part = 0   # 万 accumulator

    for ch in s:
        if ch in _ZH_DIGITS:
            current = _ZH_DIGITS[ch]
        elif ch == "十" or ch == "拾":
            if current == 0 and result == 0 and wan_part == 0 and yi_part == 0:
                current = 1  # "十二" = 12, implied 一
            result += current * 10
            current = 0
        elif ch == "百" or ch == "佰":
            result += current * 100
            current = 0
        elif ch == "千" or ch == "仟":
            result += current * 1000
            current = 0
        elif ch == "万":
            result += current
            wan_part = result * 10_000
            result = 0
            current = 0
        elif ch == "亿":
            result += current
            yi_part = (yi_part + wan_part + result) * 100_000_000
            wan_part = 0
            result = 0
            current = 0

    result += current
    return yi_part + wan_part + result


def _convert_zh_number(match: re.Match, original_text: str) -> str:
    """Convert a matched Chinese number span to Arabic digits."""
    text = match.group()
    if not text:
        return text

    negative = False
    core = text
    if core[0] in "负負":
        negative = True
        core = core[1:]

    # Handle decimal: "三点一四" → "3.14"
    if "点" in core:
        parts = core.split("点", 1)
        integer_part = _parse_zh_integer(parts[0]) if parts[0] else 0
        # Decimal digits are read one-by-one
        decimal_str = ""
        for ch in parts[1]:
            if ch in _ZH_DIGITS:
                decimal_str += str(_ZH_DIGITS[ch])
        if integer_part is None:
            integer_part = 0
        result = f"{integer_part}.{decimal_str}" if decimal_str else str(integer_part)
    else:
        val = _parse_zh_integer(core)
        if val is None:
            return match.group()
        result = str(val)

    if negative:
        result = "-" + result
    return result


# ── English number mapping ───────────────────────────────────────────────

_EN_ONES: dict[str, int] = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4,
    "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
    "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13,
    "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17,
    "eighteen": 18, "nineteen": 19,
}

_EN_TENS: dict[str, int] = {
    "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50,
    "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90,
}

_EN_POWERS: dict[str, int] = {
    "hundred": 100, "thousand": 1000, "million": 1_000_000,
    "billion": 1_000_000_000, "trillion": 1_000_000_000_000,
}

_EN_NUM_RE = re.compile(
    r"\b(?:(?:one|two|three|four|five|six|seven|eight|nine|ten|"
    r"eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|"
    r"eighteen|nineteen|twenty|thirty|forty|fifty|sixty|seventy|"
    r"eighty|ninety|zero|hundred|thousand|million|billion|trillion|and|a)"
    r"(?:\s+|-)){2,}(?:one|two|three|four|five|six|seven|eight|nine|ten|"
    r"eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|"
    r"eighteen|nineteen|twenty|thirty|forty|fifty|sixty|seventy|"
    r"eighty|ninety|zero|hundred|thousand|million|billion|trillion)\b",
    re.IGNORECASE,
)


def _parse_en_number(s: str) -> int | None:
    """Parse English number words like 'one thousand two hundred' → 1200."""
    words = re.split(r"[\s-]+", s.lower().strip())
    words = [w for w in words if w and w != "and"]

    if not words:
        return None

    result = 0
    current = 0

    for w in words:
        if w == "a":
            current = 1
        elif w in _EN_ONES:
            current += _EN_ONES[w]
        elif w in _EN_TENS:
            current += _EN_TENS[w]
        elif w == "hundred":
            if current == 0:
                current = 1
            current *= 100
        elif w in ("thousand", "million", "billion", "trillion"):
            if current == 0:
                current = 1
            current *= _EN_POWERS[w]
            result += current
            current = 0
        else:
            return None  # unknown word, bail

    return result + current


def _convert_en_number(match: re.Match) -> str:
    """Convert matched English number words to Arabic digits."""
    val = _parse_en_number(match.group())
    if val is not None:
        return str(val)
    return match.group()


# ── Public API ───────────────────────────────────────────────────────────

def normalize_numbers(text: str) -> str:
    """Apply Inverse Text Normalization: convert spoken numbers to digits.

    Handles both Chinese and English number expressions.
    Non-number text is left unchanged.
    Context-aware: preserves "一下", "一些", "一起" etc.
    """
    if not text:
        return text

    # Chinese numbers — context-aware substitution
    def _zh_replacer(m: re.Match) -> str:
        if _should_convert_zh(m, text):
            return _convert_zh_number(m, text)
        return m.group()

    result = _ZH_NUM_RE.sub(_zh_replacer, text)

    # English numbers
    result = _EN_NUM_RE.sub(_convert_en_number, result)

    return result
