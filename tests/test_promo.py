"""Recommend prompts and the after-update note: when they show and what the shared text says."""
from urllib.parse import parse_qs, urlparse

import pytest

from thundertalk.core import i18n, promo


# ── milestones ──
def test_no_milestone_before_ten_hours():
    assert promo.milestone_due(10 * 3600 - 1, 0, False) is None


def test_each_milestone_is_due_once():
    assert promo.milestone_due(10 * 3600, 0, False) == 10
    assert promo.milestone_due(49 * 3600, 10, False) is None        # acknowledged; the next one is 50 h
    assert promo.milestone_due(50 * 3600, 10, False) == 50


def test_a_long_time_user_gets_one_prompt_not_one_per_milestone_passed():
    assert promo.milestone_due(120 * 3600, 0, False) == 100         # the highest reached
    assert promo.milestone_due(120 * 3600, 100, False) is None


def test_switched_off_means_never():
    assert promo.milestone_due(1000 * 3600, 0, True) is None


# ── share text ──
def test_language_follows_the_app_then_the_mac():
    assert promo.share_language("zh", ["en-US"]) == "zh"
    assert promo.share_language("en", ["zh-Hans-CN", "en-US"]) == "zh"   # English UI on a Chinese Mac
    assert promo.share_language("en", ["en-US", "zh-Hans-CN"]) == "en"
    assert promo.share_language("en", []) == "en"


@pytest.mark.parametrize("lang", ["en", "zh"])
def test_places_that_take_links_get_the_link_and_the_others_never_do(lang):
    for d in promo.DESTINATIONS[lang]:
        text = promo.share_text(lang, d.id)
        if d.link:
            assert promo.SITE_URL in text
        else:
            assert "http" not in text and "://" not in text and ".io" not in text
            assert "GitHub" in text and "ThunderTalk" in text       # how to find it without a link


def test_xiaohongshu_and_douyin_are_the_no_link_place_in_chinese():
    d = promo.destination("zh", "xiaohongshu")
    assert not d.link and "小红书" in d.label and "抖音" in d.label
    assert promo.destination("zh", "bilibili").link and promo.destination("zh", "wechat").link


def test_link_only_is_just_the_link():
    assert promo.share_text("en", "link") == promo.SITE_URL == promo.share_text("zh", "link")


@pytest.mark.parametrize("lang", ["en", "zh"])
def test_the_x_text_fits_a_post_and_the_composer_url_carries_it(lang):
    text = promo.share_text(lang, "x")
    assert promo.x_weight(text) <= 280
    url = promo.compose_url(lang, "x")
    assert urlparse(url).netloc == "x.com"
    assert parse_qs(urlparse(url).query)["text"] == [text]
    assert promo.compose_url(lang, "link") == ""


def test_x_counts_wide_characters_twice_and_a_url_as_23():
    assert promo.x_weight("abc") == 3
    assert promo.x_weight("中文") == 4
    assert promo.x_weight("a https://example.com/a/very/long/path/that/is/longer/than/23") == 1 + 1 + 23


def test_an_unknown_place_falls_back_to_the_first():
    assert promo.share_text("en", "nowhere") == promo.share_text("en", "x")


def test_texts_make_no_claims_the_app_cannot_back():
    for text in promo._TEXTS.values():
        low = text.lower()
        assert "best" not in low and "fastest" not in low and "最好" not in text and "最快" not in text


# ── what's new ──
def test_a_new_install_is_told_nothing():
    assert promo.whats_new("1.17.0", "", "") is None


def test_an_upgrade_shows_the_notes_once():
    version, keys = promo.whats_new("1.17.0", "", "1.16.0")
    assert version == "1.17.0" and 1 <= len(keys) <= 3
    assert promo.whats_new("1.17.0", "1.17.0", "1.17.0") is None     # closed


def test_a_version_without_notes_shows_nothing_and_never_notes_from_the_future():
    assert promo.whats_new("1.16.0", "", "1.15.0") is None
    assert promo.whats_new("1.17.1", "1.17.0", "1.17.0") is None
    assert promo.whats_new("1.17.1", "", "1.16.0")[0] == "1.17.0"    # skipped a version: its notes still count


def test_every_note_has_text_in_both_languages():
    for keys in promo.WHATS_NEW.values():
        assert len(keys) <= 3
        for key in keys:
            for part in ("", ".detail"):
                entry = i18n._STRINGS[key + part]
                assert entry["en"].strip() and entry["zh"].strip()


# ── which popup, and when ──
def _next(**kw):
    base = dict(current="1.17.0", seen="1.17.0", last_run="1.17.0", saved_secs=0.0, acknowledged_hours=0,
                off=False, setup_done=True, shown_this_launch=False)
    return promo.next_popup(**{**base, **kw})


def test_nothing_pops_up_for_someone_who_has_nothing_to_hear():
    assert _next() is None


def test_the_update_note_comes_before_the_milestone_and_only_one_per_launch():
    both = dict(seen="", last_run="1.16.0", saved_secs=12 * 3600)
    assert _next(**both)[0] == "whatsnew"
    assert _next(**both, shown_this_launch=True) is None
    assert _next(saved_secs=12 * 3600) == ("milestone", 10, 12)      # later launch: milestone 10, "about 12 hours"


def test_nothing_pops_up_during_setup_or_once_switched_off():
    assert _next(saved_secs=12 * 3600, setup_done=False) is None
    assert _next(saved_secs=12 * 3600, off=True) is None
    assert _next(seen="", last_run="1.16.0", off=True)[0] == "whatsnew"   # the switch is about recommending only


def test_time_saved_is_the_same_figure_home_shows():
    assert promo.saved_seconds(4000, 600.0) == 4000 / 40 * 60 - 600
    assert promo.saved_seconds(10, 600.0) == 0.0


# ── the windows themselves ──
@pytest.fixture
def qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def _texts(widget):
    from PySide6.QtWidgets import QLabel, QPushButton
    return [w.text() for w in widget.findChildren(QLabel)] + [w.text() for w in widget.findChildren(QPushButton)]


def test_the_recommend_window_follows_the_place_and_copies_what_it_shows(qapp, monkeypatch):
    from thundertalk.ui import recommend_dialog as rd
    opened = []
    monkeypatch.setattr(rd.webbrowser, "open", opened.append)
    d = rd.RecommendDialog(None, "zh")
    d.select("xiaohongshu")
    assert "http" not in d.text and "GitHub" in d.text
    d._on_copy()
    assert qapp.clipboard().text() == d.text and opened == []        # no composer for this place
    d.select("x")
    assert promo.SITE_URL in d.text
    d._on_copy()
    assert qapp.clipboard().text() == d.text and opened and opened[0].startswith("https://x.com/intent/post?text=")
    d.set_language("en")
    assert d.text == promo.share_text("en", "x")                      # same place, English text
    assert "小红书 · 抖音" not in _texts(d)


def test_the_text_can_be_edited_and_the_edit_is_what_gets_copied_and_posted(qapp, monkeypatch):
    from thundertalk.ui import recommend_dialog as rd
    opened = []
    monkeypatch.setattr(rd.webbrowser, "open", opened.append)
    d = rd.RecommendDialog(None, "en")
    d.select("x")
    d._text.setPlainText("my own words " + promo.SITE_URL)
    d._on_copy()
    assert qapp.clipboard().text() == "my own words " + promo.SITE_URL
    assert parse_qs(urlparse(opened[0]).query)["text"] == ["my own words " + promo.SITE_URL]
    d._copied.timeout.emit()                                          # the button label goes back; the text stays
    d.select("x")
    assert d.text == "my own words " + promo.SITE_URL
    d.select("message")
    assert d.text == promo.share_text("en", "message")               # another place: its own ready text


def test_the_ready_text_describes_the_app_and_does_not_speak_for_the_sender():
    for text in promo._TEXTS.values():
        assert text.startswith("ThunderTalk")
        low = f" {text.lower()} "
        assert " i " not in low and " my " not in low and "我" not in text


def test_the_milestone_window_reports_the_choice(qapp):
    from thundertalk.ui.promo_popups import MilestoneDialog
    d = MilestoneDialog(None, 12)
    assert any("12" in x for x in _texts(d))
    assert d.choice == MilestoneDialog.LATER                          # closing it any other way = not now
    d._choose(MilestoneDialog.STAR)
    assert d.choice == MilestoneDialog.STAR


def test_the_update_window_lists_the_notes(qapp):
    from thundertalk.ui.promo_popups import WhatsNewDialog
    d = WhatsNewDialog(None, "1.17.0", promo.WHATS_NEW["1.17.0"])
    texts = _texts(d)
    assert d.version == "1.17.0"                                      # the release notes it links to
    for key in promo.WHATS_NEW["1.17.0"]:
        assert any(i18n.t(key) in x and i18n.t(key + ".detail") in x for x in texts)


class _Settings(dict):
    def set(self, key, value):
        self[key] = value


def test_a_new_install_is_marked_as_having_seen_this_versions_notes(qapp):
    import thundertalk
    from PySide6.QtWidgets import QWidget
    from thundertalk.ui.promo_popups import PromoPopups
    s = _Settings(last_run_version="", onboarding_done=True)
    history = type("H", (), {"total_units": 0, "total_duration_secs": 0.0})()
    p = PromoPopups(QWidget(), s, history)
    assert s["whats_new_seen"] == thundertalk.__version__ and p.pending() is None


def test_popups_wait_until_the_apps_own_window_is_in_front(qapp, monkeypatch):
    from PySide6.QtWidgets import QWidget
    from thundertalk.ui import promo_popups as pp
    s = _Settings(last_run_version="1.16.0", onboarding_done=True, whats_new_seen="9.9.9")
    history = type("H", (), {"total_units": 100_000, "total_duration_secs": 3600.0})()
    window = QWidget()                                                # never shown: the user is elsewhere
    p = pp.PromoPopups(window, s, history)
    opened = []
    monkeypatch.setattr(pp.MilestoneDialog, "open_over", lambda self, parent: opened.append(self) or 0)
    assert p.pending()[0] == "milestone"
    p._show()
    assert opened == [] and "promo_milestone_hours" not in s          # nothing shown, nothing used up
    monkeypatch.setattr(p, "_in_the_way", lambda: False)
    p._show()
    assert len(opened) == 1 and s["promo_milestone_hours"] == 10      # "not now" still counts as heard
    p._show()
    assert len(opened) == 1                                           # one per launch
