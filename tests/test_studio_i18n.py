"""Every string key the Studio asks for exists in both languages with the same
placeholders — a missing key would show the raw key to a user."""

from __future__ import annotations

import pathlib
import re


from thundertalk.core import i18n, tts

ROOT = pathlib.Path(__file__).resolve().parents[1] / "thundertalk"
SOURCES = list((ROOT / "ui" / "studio").glob("*.py")) + [ROOT / "ui" / "pages" / "studio_page.py",
                                                         ROOT / "ui" / "main_window.py"]
STATIC = re.compile(r'\bt\(\s*"([a-z_.0-9]+)"')
CONDITIONAL = re.compile(r'"(studio\.[a-z_.0-9]+)"')


def _keys() -> set[str]:
    out: set[str] = set()
    for f in SOURCES:
        text = f.read_text(encoding="utf-8")
        out |= set(STATIC.findall(text))
        out |= set(CONDITIONAL.findall(text))
    out |= {f"studio.lang.{c}" for c in tts.LANGUAGES}
    out |= {f"studio.err.{c}" for c in ("no_speech", "no_model", "tts_missing", "memory")}
    out |= {f"studio.progress.{c}" for c in ("decode", "load_moss", "diarize")}
    out |= {f"studio.clone.warn.{c}" for c in ("clipping", "trimmed_long", "too_quiet", "short")}
    return {k for k in out if k.startswith(("studio.", "nav.studio", "common.cancel"))}


def test_every_studio_key_exists_in_both_languages():
    missing = [k for k in sorted(_keys()) if k not in i18n._STRINGS]
    assert not missing, missing
    incomplete = [k for k in sorted(_keys()) if not (i18n._STRINGS[k].get("en") and i18n._STRINGS[k].get("zh"))]
    assert not incomplete, incomplete


def test_placeholders_match_between_languages():
    bad = []
    for k, v in i18n._STRINGS.items():
        if k.startswith("studio."):
            if set(re.findall(r"{(\w+)}", v["en"])) != set(re.findall(r"{(\w+)}", v["zh"])):
                bad.append(k)
    assert not bad, bad


def test_preset_voices_have_blurbs_in_both_languages():
    for v in tts.PRESET_VOICES:
        assert v.blurb_en and v.blurb_zh


def test_old_lab_strings_are_gone():
    assert not [k for k in i18n._STRINGS if k.startswith("lab.")]
    assert "nav.lab" not in i18n._STRINGS
