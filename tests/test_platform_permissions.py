import sys

import pytest

from thundertalk.core import platform_utils as pu


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS only")
def test_objc_mic_status_is_a_known_value() -> None:
    """The ctypes AVFoundation call must work without pyobjc's AVFoundation
    binding (which is not a dependency) instead of silently reporting
    'authorized' for everyone."""
    status = pu._mic_status_via_objc()
    assert status in ("not_determined", "restricted", "denied", "authorized")


def test_check_microphone_returns_known_value() -> None:
    assert pu.check_microphone() in ("not_determined", "restricted", "denied", "authorized")


def test_status_table_matches_avfoundation_enum() -> None:
    assert pu._MIC_STATUS == {0: "not_determined", 1: "restricted", 2: "denied", 3: "authorized"}
