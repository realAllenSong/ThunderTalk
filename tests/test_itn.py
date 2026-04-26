"""ITN regression tests.

Origin of the suite: a user reported that "百分之五" was being
post-processed to "0分之5" instead of either "百分之五" or "5%". Root
cause: `_parse_zh_integer("百")` returns 0 because there is no leading
digit and the parser does `current * 100 = 0 * 100`, while
`_should_convert_zh` still routes the bare unit through conversion.
The same defect affected every bare-unit-only token where the unit
isn't 十/拾 (which IS special-cased to mean 10): 百, 佰, 千, 仟,
万, 亿.

These tests pin both the broken cases (now fixed) and a panel of
regressions to make sure the fix doesn't break valid number
conversion.
"""

from __future__ import annotations

import pytest

from thundertalk.core.itn import normalize_numbers


# ── Bug repro: bare unit chars must NOT collapse to 0 ──────────────
# (百分之X cases removed from this panel — they're now expected to
# convert all the way to "X%"; see test_percent_conversion below.)

@pytest.mark.parametrize(
    "spoken, expected",
    [
        ("百年", "百年"),
        ("百货", "百货"),
        ("百姓", "百姓"),
        ("千克", "千克"),
        ("千万别这样", "千万别这样"),
        # "万分之一" is itself a 4-char idiom ("extremely small chance");
        # the new idiom-density rule preserves the trailing 一 along
        # with the bare 万. Both stay Chinese, matching the phrase's
        # idiomatic reading.
        ("万分之一", "万分之一"),
        ("百", "百"),
        ("千", "千"),
        ("万", "万"),
    ],
)
def test_bare_unit_not_converted(spoken: str, expected: str) -> None:
    assert normalize_numbers(spoken) == expected


# ── 百分之X → X% (full conversion, not just "百分之5") ─────────────

@pytest.mark.parametrize(
    "spoken, expected",
    [
        # Plain Chinese numerator
        ("百分之五", "5%"),
        ("百分之十", "10%"),
        ("百分之二十五", "25%"),
        ("百分之零", "0%"),
        # Unit-only numerator (special case: 百 in denominator-position
        # of 百分之百 means 100, not 0)
        ("百分之百", "100%"),
        ("百分之一百", "100%"),
        # Decimal numerator
        ("百分之零点五", "0.5%"),
        ("百分之八十一点五", "81.5%"),
        # ASR pre-digitized (some models emit digits already)
        ("百分之5", "5%"),
        ("百分之100", "100%"),
        ("百分之0.5", "0.5%"),
        # In a sentence
        ("我有百分之百的把握", "我有100%的把握"),
        ("增长了百分之五", "增长了5%"),
        ("百分之十五的概率", "15%的概率"),
        # Two separate occurrences
        ("百分之十和百分之二十", "10%和20%"),
        # Non-numeric tail must NOT match (no number after 之)
        ("百分之多少", "百分之多少"),
        ("百分之几", "百分之几"),
    ],
)
def test_percent_conversion(spoken: str, expected: str) -> None:
    assert normalize_numbers(spoken) == expected


# ── Regression: digit + unit combos must still convert ─────────────

@pytest.mark.parametrize(
    "spoken, expected",
    [
        ("一百", "100"),
        ("两百G", "200G"),
        ("三百五十二", "352"),
        ("一千", "1000"),
        ("两千零三", "2003"),
        ("一万", "10000"),
        ("一亿", "100000000"),
    ],
)
def test_digit_plus_unit_still_converts(spoken: str, expected: str) -> None:
    assert normalize_numbers(spoken) == expected


# ── Regression: 十/拾 standalone stays the documented 10 ───────────

@pytest.mark.parametrize(
    "spoken, expected",
    [
        ("十", "10"),
        ("二十", "20"),
        ("十六还是六十四", "16还是64"),
    ],
)
def test_ten_special_case_unchanged(spoken: str, expected: str) -> None:
    assert normalize_numbers(spoken) == expected


# ── "万一" idiom (and friends): unit + bare digit must NOT collapse ──
# User report: speaking "万一" came out as "1" — the 万 was dropped
# entirely. _parse_zh_integer treats 万一 as (current=0)*10000 + 1 = 1
# because there's no leading digit before the unit. The phrase 万一
# means "in case", not the number 1; 千一/百一/亿一 are similar
# patterns that should also be left alone. 十X stays special-cased to
# 1X (十一=11, 十六=16) since that IS how Chinese spells 11-19.

@pytest.mark.parametrize(
    "spoken, expected",
    [
        ("万一", "万一"),
        ("千一", "千一"),
        ("百一", "百一"),
        ("亿一", "亿一"),
        ("万一发生", "万一发生"),
        ("假如万一", "假如万一"),
        ("万一中奖了", "万一中奖了"),
    ],
)
def test_unit_plus_bare_digit_idiom_not_converted(
    spoken: str, expected: str
) -> None:
    assert normalize_numbers(spoken) == expected


@pytest.mark.parametrize(
    "spoken, expected",
    [
        # 十X must STILL convert (10s family is real number spelling)
        ("十一", "11"),
        ("十二", "12"),
        ("十六还是六十四", "16还是64"),
        # And longer compounds still work
        ("一万", "10000"),
        ("一万一", "10001"),  # preserves existing behavior
    ],
)
def test_ten_family_and_compounds_still_convert(
    spoken: str, expected: str
) -> None:
    assert normalize_numbers(spoken) == expected


# ── 4-char 成语 (idioms): single digit chars wedged in CJK runs ─────
# User report: "一发不可收拾" came out as "1发不可收10" — both the
# leading 一 and the trailing 拾 (which gets parsed as 10 like 十)
# were converted. Generalizing: any single-char number embedded in
# 3+ surrounding CJK chars is almost certainly part of a Chinese
# idiom, not a digit. Bare 拾 is also never modern 10 — only 十 is.

@pytest.mark.parametrize(
    "spoken, expected",
    [
        # The user's report
        ("一发不可收拾", "一发不可收拾"),
        # Other common 4-char idioms with leading 一
        ("一鸣惊人", "一鸣惊人"),
        ("一蹴而就", "一蹴而就"),
        ("一帆风顺", "一帆风顺"),
        ("一目了然", "一目了然"),
        ("一举两得", "一举两得"),  # both 一 and 两 must be skipped
        # Idiom with the digit in the middle
        ("万无一失", "万无一失"),
        # Bare 拾 (modern Chinese — 10 is 十, not 拾)
        ("拾", "拾"),
        ("收拾", "收拾"),
        ("打扫收拾", "打扫收拾"),
    ],
)
def test_idiom_protection(spoken: str, expected: str) -> None:
    assert normalize_numbers(spoken) == expected


# ── Counters / measure words / numeric predicates STILL convert ──
# The idiom-density heuristic must defer to clear "this is a number"
# signals so the user keeps getting "1次" / "1年" / "1是不是太低".

@pytest.mark.parametrize(
    "spoken, expected",
    [
        ("一次", "1次"),
        ("上一次", "上1次"),
        ("一年", "1年"),
        ("一月", "1月"),
        ("一日", "1日"),
        ("一号", "1号"),
        ("一岁", "1岁"),
        ("一倍", "1倍"),
        # Predicate "X 是 Y" — the user often says "1 is not too low?"
        ("一是不是太低", "1是不是太低"),
        ("一是不是有点太低了", "1是不是有点太低了"),
        # Stop-suffix list still wins (preserve colloquial form)
        ("一个", "一个"),
        ("三个苹果", "三个苹果"),
        ("一块钱", "一块钱"),
        # Multi-char compounds keep working
        ("三十五", "35"),
        ("一百", "100"),
        ("三百五十二", "352"),
        # 二拾 (formal-ish, with leading digit) — still 20
        ("二拾", "20"),
    ],
)
def test_counter_and_compounds_unchanged(
    spoken: str, expected: str
) -> None:
    assert normalize_numbers(spoken) == expected
