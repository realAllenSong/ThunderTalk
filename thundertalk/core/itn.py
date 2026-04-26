"""Inverse Text Normalization (ITN) — convert spoken numbers to digits.

Lightweight, zero-dependency post-processor:
    "一千"             → "1000"
    "三百五十二"        → "352"
    "batch size为一"    → "batch size为1"
    "十六还是六十四"     → "16还是64"
    "两百G"            → "200G"
    "twenty five"      → "25"
    "two hundred"      → "200"
    "M B S"            → "MBS"
    "《一千零一夜》"     → "《一千零一夜》"  (preserved in titles)
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

_ZH_NO_CONVERT_SUFFIXES = frozenset(
    "下些起直定般样边旦共向味致贯概度番遍切而再且又并非但"
    "来去面路带辈阵子把个只条件层片块位场双节本张道口间"
    "种类份段组套批根台架坛所门座栋间扇期集首部册卷幅"
    "堆群排列名头只匹支枚颗粒滴缕丝股串捆扎簇束团伙帮拨"
)

_ZH_NO_CONVERT_PREFIXES = frozenset("第统")

# Counter / measure-word characters that strongly mark "the digit
# before me is a real number". When a single digit char is followed
# by one of these, conversion is forced through even if the broader
# idiom-density heuristic would otherwise skip — "上一次" → "上1次"
# / "一年" → "1年". Intentionally excludes 个/张/块/本/etc., which
# are already in _ZH_NO_CONVERT_SUFFIXES (preserve colloquial 一X
# spelling).
_ZH_MEASURE_WORDS = frozenset("次年月日号周岁元倍")

# "X 是 Y" reads as a numeric predication ("1 is not too low?") and
# the user expects digit form. Keep this small — only the copula 是.
_ZH_NUMERIC_PREDICATE_NEXT = frozenset("是")


def _is_cjk(ch: str) -> bool:
    return "一" <= ch <= "鿿"


def _cjk_density_around(text: str, start: int, end: int) -> int:
    """Total contiguous CJK chars within 3 positions on each side of
    a single-char number match. Used as a 4-char-idiom (成语) signal:
    if the match is wedged in 3+ CJK runs, it's almost always part of
    a fixed phrase rather than a real number."""
    cjk_after = 0
    for i in range(end, min(end + 3, len(text))):
        if _is_cjk(text[i]):
            cjk_after += 1
        else:
            break
    cjk_before = 0
    for i in range(start - 1, max(start - 4, -1), -1):
        if _is_cjk(text[i]):
            cjk_before += 1
        else:
            break
    return cjk_before + cjk_after

_ZH_NUM_RE = re.compile(
    r"[负負]?"
    r"[零〇一二两三四五六七八九十拾百佰千仟万亿]+"
    r"(?:点[零〇一二两三四五六七八九]+)?"
)

_ZH_TITLE_RE = re.compile(r"《[^》]*》")

# "百分之X" → "X%". Handled BEFORE the general number sub so the
# whole spoken phrase is treated as one unit ("百分之百" must collapse
# to "100%", not be fragmented into "百" + "分之" + "百"). The
# numerator can be either Chinese spelled-out (with optional decimal
# part) OR already-digitized form that the ASR may emit.
_ZH_PERCENT_RE = re.compile(
    r"百分之("
    r"[负負]?"
    r"(?:[零〇一二两三四五六七八九十拾百佰千仟万亿]+(?:点[零〇一二两三四五六七八九]+)?"
    r"|\d+(?:\.\d+)?)"
    r")"
)


def _build_title_ranges(text: str) -> list[tuple[int, int]]:
    """Find all 《…》 spans so we can skip number conversion inside titles."""
    return [(m.start(), m.end()) for m in _ZH_TITLE_RE.finditer(text)]


def _in_title(pos: int, ranges: list[tuple[int, int]]) -> bool:
    return any(s <= pos < e for s, e in ranges)


def _should_convert_zh(match: re.Match, original_text: str, title_ranges: list[tuple[int, int]]) -> bool:
    if _in_title(match.start(), title_ranges):
        return False

    text = match.group()
    core = text.lstrip("负負")

    # Bare unit-only matches like "百" / "千" / "万" / "亿" (no digits,
    # no decimal point) are almost always parts of fixed phrases —
    # 百分之, 百年, 百货, 千克, 千万, 万分之 — rather than standalone
    # numbers. Without a leading digit, _parse_zh_integer returns 0
    # (because current * 100 = 0 * 100), so passing them through
    # would corrupt "百分之五" into "0分之5". 十/拾 IS the exception
    # because it's idiomatically spoken as the number 10
    # (十块钱 / 数十), and _parse_zh_integer special-cases it.
    has_digit = any(c in _ZH_DIGITS for c in core)
    has_decimal = "点" in core
    if not has_digit and not has_decimal:
        # 十 alone is idiomatically "ten" (十块 / 数十). 拾 was previously
        # included here too, but in modern Chinese 拾 only appears in
        # words like 收拾 / 拾遗 / 拾级, never as a standalone "10"
        # (the formal 10 is 壹拾, not 拾). Letting it through made
        # "一发不可收拾" emit "一发不可收10".
        if not all(c == "十" for c in core):
            return False

    # Two-char "[unit][digit]" idioms (万一, 千一, 百一, 亿一) read in
    # speech as fixed phrases — 万一 = "in case", not the number 1 —
    # but _parse_zh_integer treats them as (current=0 × magnitude) +
    # digit, dropping the unit entirely. Skip them.
    # 十X is exempt: that IS how Chinese spells 11-19 and the parser
    # handles it correctly via the bare-十 special case.
    if (
        len(core) == 2
        and core[0] in ("百", "佰", "千", "仟", "万", "亿")
        and core[1] in _ZH_DIGITS
    ):
        return False

    if any(c in _ZH_UNIT_CHARS for c in core):
        return True

    if "点" in core:
        return True

    digit_count = sum(1 for c in core if c in _ZH_DIGITS)
    if digit_count >= 2:
        return True

    # Single Chinese digit char in 4-char-idiom (成语) context.
    # User report: "一发不可收拾" → "1发不可收10". Generalising:
    # almost any single 一-九 wedged in a CJK run of 3+ surrounding
    # chars is part of a fixed phrase, not a literal number —
    # 一鸣惊人 / 一蹴而就 / 一帆风顺 / 万无一失 / 一举两得 …
    # Two escape hatches preserve the user's existing conversions:
    #   * Next char is a clear measure word → "上一次" / "一年" / "一日".
    #   * Next char is the copula 是, signalling the predicate
    #     pattern "X 是 Y" ("1 is not too low?") — rare construction
    #     but the user uses it for config-value questions.
    if digit_count == 1 and len(core) == 1:
        next_idx = match.end()
        next_char = original_text[next_idx] if next_idx < len(original_text) else ""
        if (
            next_char not in _ZH_MEASURE_WORDS
            and next_char not in _ZH_NUMERIC_PREDICATE_NEXT
            and _cjk_density_around(
                original_text, match.start(), match.end()
            ) >= 3
        ):
            return False

    end = match.end()
    if end < len(original_text):
        next_char = original_text[end]
        if next_char in _ZH_NO_CONVERT_SUFFIXES:
            return False

    start = match.start()
    if start > 0:
        prev_char = original_text[start - 1]
        if prev_char in _ZH_NO_CONVERT_PREFIXES:
            return False

    if core in ("零", "〇"):
        return False

    return True


def _parse_zh_integer(s: str) -> int | None:
    if not s:
        return None

    if all(c in _ZH_DIGITS for c in s) and len(s) > 1:
        return int("".join(str(_ZH_DIGITS[c]) for c in s))

    result = 0
    current = 0
    yi_part = 0
    wan_part = 0

    for ch in s:
        if ch in _ZH_DIGITS:
            current = _ZH_DIGITS[ch]
        elif ch == "十" or ch == "拾":
            if current == 0 and result == 0 and wan_part == 0 and yi_part == 0:
                current = 1
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


_DIGIT_NUM_RE = re.compile(r"\d+(?:\.\d+)?")


def _convert_percent(match: re.Match) -> str:
    """Turn '百分之X' (X = Chinese number or digit) into 'X%'.

    Special case: in this fixed phrase the *value* of a bare unit
    char like 百 is its literal magnitude — '百分之百' means 100%, not
    0%. Without that override the bare-unit fix from the previous
    patch would emit '0%' here.
    """
    body = match.group(1)

    if _DIGIT_NUM_RE.fullmatch(body):
        return f"{body}%"

    negative = False
    if body and body[0] in ("负", "負"):
        negative = True
        body = body[1:]

    if "点" in body:
        int_part, _, dec_part = body.partition("点")
        int_val = _parse_zh_integer(int_part) if int_part else 0
        if int_val is None:
            return match.group()
        decimal_str = "".join(
            str(_ZH_DIGITS[c]) for c in dec_part if c in _ZH_DIGITS
        )
        out = f"{int_val}.{decimal_str}" if decimal_str else str(int_val)
    else:
        val = _parse_zh_integer(body)
        if val is None:
            return match.group()
        # Bare-unit override (only meaningful in percent context).
        if val == 0:
            if body in ("百", "佰"):
                val = 100
            elif body in ("千", "仟"):
                val = 1000
            elif body in ("万",):
                val = 10000
        out = str(val)

    if negative:
        out = "-" + out
    return f"{out}%"


def _convert_zh_number(match: re.Match, original_text: str) -> str:
    text = match.group()
    if not text:
        return text

    negative = False
    core = text
    if core[0] in "负負":
        negative = True
        core = core[1:]

    if "点" in core:
        parts = core.split("点", 1)
        integer_part = _parse_zh_integer(parts[0]) if parts[0] else 0
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

_EN_ALL_WORDS = set(_EN_ONES) | set(_EN_TENS) | set(_EN_POWERS) | {"a", "and"}

_EN_NUM_RE = re.compile(
    r"\b(?:(?:one|two|three|four|five|six|seven|eight|nine|ten|"
    r"eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|"
    r"eighteen|nineteen|twenty|thirty|forty|fifty|sixty|seventy|"
    r"eighty|ninety|zero|hundred|thousand|million|billion|trillion|and|a)"
    r"(?:\s+|-))+"
    r"(?:one|two|three|four|five|six|seven|eight|nine|ten|"
    r"eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|"
    r"eighteen|nineteen|twenty|thirty|forty|fifty|sixty|seventy|"
    r"eighty|ninety|zero|hundred|thousand|million|billion|trillion)\b",
    re.IGNORECASE,
)


def _parse_en_number(s: str) -> int | None:
    """Parse English number words. Returns None if structure is invalid
    (e.g., same power level appears twice → two numbers run together).
    """
    words = re.split(r"[\s-]+", s.lower().strip())
    words = [w for w in words if w and w != "and"]

    if not words:
        return None
    if not all(w in _EN_ALL_WORDS for w in words):
        return None

    result = 0
    current = 0
    last_power = float("inf")

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
            power = _EN_POWERS[w]
            if power >= last_power:
                return None
            last_power = power
            if current == 0:
                current = 1
            current *= power
            result += current
            current = 0

    return result + current


_EN_POWER_WORDS = {"hundred", "thousand", "million", "billion", "trillion"}


def _split_en_numbers(span: str) -> str:
    """Try parsing as one number; if ambiguous, split at the best boundary."""
    val = _parse_en_number(span)
    if val is not None:
        return str(val)

    parts = re.split(r"([\s-]+)", span)
    words = [parts[i] for i in range(0, len(parts), 2)]
    seps = [parts[i] for i in range(1, len(parts), 2)]

    num_words = [w.lower() for w in words]

    best = None
    for i in range(2, len(num_words)):
        lv = _parse_en_number(" ".join(num_words[:i]))
        rv = _parse_en_number(" ".join(num_words[i:]))
        if lv is None or rv is None:
            continue
        if num_words[i] in _EN_POWER_WORDS:
            continue
        best = (i, lv, rv)

    if best:
        idx, lv, rv = best
        sep = seps[idx - 1] if idx - 1 < len(seps) else " "
        return f"{lv}{sep}{rv}"

    return span


def _convert_en_number(match: re.Match) -> str:
    return _split_en_numbers(match.group())


# ── Letter merging (M B S → MBS) ─────────────────────────────────────────

_LETTER_SPACES_RE = re.compile(
    r"(?<![A-Za-z])"
    r"([A-Za-z])"
    r"(?:\s([A-Za-z]))"
    r"(?:\s([A-Za-z]))*"
    r"(?![A-Za-z])"
)


def _merge_spaced_letters(text: str) -> str:
    """Merge isolated single letters separated by spaces: 'M B S' → 'MBS'."""
    def _replacer(m: re.Match) -> str:
        span = m.group()
        letters = re.findall(r"[A-Za-z]", span)
        if len(letters) >= 2:
            return "".join(letters)
        return span

    return _LETTER_SPACES_RE.sub(_replacer, text)


# ── Public API ───────────────────────────────────────────────────────────

def normalize_numbers(text: str) -> str:
    """Apply Inverse Text Normalization: convert spoken numbers to digits,
    merge spaced single letters. Preserves text inside 《》 titles.
    """
    if not text:
        return text

    title_ranges = _build_title_ranges(text)

    # Percent first — "百分之X" collapses to "X%" as one unit. Done
    # before the general number sub so the whole phrase is matched
    # against the original input (so "百分之百" reaches _convert_percent
    # intact instead of being fragmented by the bare-unit number sub).
    def _percent_replacer(m: re.Match) -> str:
        if _in_title(m.start(), title_ranges):
            return m.group()
        return _convert_percent(m)

    result = _ZH_PERCENT_RE.sub(_percent_replacer, text)

    # Title ranges may have shifted after the percent substitution
    # (replaced span widths differ from original); recompute against
    # the post-percent string before the main number pass.
    title_ranges = _build_title_ranges(result)

    def _zh_replacer(m: re.Match) -> str:
        if _should_convert_zh(m, result, title_ranges):
            return _convert_zh_number(m, result)
        return m.group()

    result = _ZH_NUM_RE.sub(_zh_replacer, result)
    result = _EN_NUM_RE.sub(_convert_en_number, result)
    result = _merge_spaced_letters(result)

    return result
