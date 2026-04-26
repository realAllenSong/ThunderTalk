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
        ("万分之一", "万分之1"),
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
