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

@pytest.mark.parametrize(
    "spoken, expected",
    [
        ("百分之五", "百分之5"),
        ("百分之十", "百分之10"),
        ("百分之二十五", "百分之25"),
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
