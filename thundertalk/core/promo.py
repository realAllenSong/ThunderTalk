"""Asking a user who already gets value from the app to pass it on, and telling
an upgraded user what changed. Pure decisions; the widgets live in ui/.

Nothing here reports anything anywhere: the only outputs are a text put on the
clipboard and, when the user asks for it, a page opened in their browser.

* ``milestone_due``: once per milestone of estimated time saved, a small window
  says so and offers "Recommend" and "Star on GitHub".
* ``share_text``: a ready text for the place the user will post it. Some places
  drop or forbid links (Xiaohongshu, Douyin), so their text says how to find
  the app instead of carrying a URL.
* ``whats_new``: after an update, the two or three things worth knowing.
* ``next_popup``: which of the two, if any, to show now. At most one per launch,
  and only over the app's own window: never while the user is working elsewhere.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence
from urllib.parse import quote

SITE_URL = "https://thundertalk.entropinia.com/"
REPO_URL = "https://github.com/realAllenSong/ThunderTalk"
RELEASE_URL = REPO_URL + "/releases/tag/v{version}"

# Estimated hours of typing saved at which the app says so, once each.
MILESTONE_HOURS = (10, 50, 100, 250, 500, 1000)
TYPING_UNITS_PER_MIN = 40.0      # the typing speed Home's "Est. time saved" compares with


def saved_seconds(units: int, speaking_secs: float) -> float:
    """Typing the same text at 40 units a minute, minus the time spent speaking it."""
    return max(0.0, units / TYPING_UNITS_PER_MIN * 60 - speaking_secs)


def milestone_due(saved_secs: float, acknowledged_hours: int, off: bool) -> Optional[int]:
    """The highest milestone reached and not yet acknowledged, or None."""
    if off:
        return None
    reached = max((h for h in MILESTONE_HOURS if saved_secs >= h * 3600), default=None)
    if reached is None or reached <= int(acknowledged_hours or 0):
        return None
    return reached


# ── sharing ───────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Destination:
    id: str
    label: str
    link: bool            # may the text carry a URL there?
    compose: str = ""     # URL that opens the place's own composer with the text ("{text}" is quoted)


_X = "https://x.com/intent/post?text={text}"

# The places offered for each language of the text, most likely first.
DESTINATIONS: dict[str, tuple[Destination, ...]] = {
    "en": (
        Destination("x", "X", True, _X),
        Destination("message", "Message or email", True),
        Destination("link", "Link only", True),
    ),
    "zh": (
        Destination("wechat", "微信", True),
        Destination("x", "X", True, _X),
        Destination("bilibili", "B站", True),
        Destination("xiaohongshu", "小红书 · 抖音", False),
        Destination("link", "只要链接", True),
    ),
}

# A plain description of the app, in the README's own words: the sender edits it into
# their own voice if they want one. Not a testimonial written on their behalf.
_ABOUT = {
    "en": "ThunderTalk: free, open-source voice input for Mac. Press a hotkey in any app, speak, and the "
          "text lands where your cursor is. Recognition runs on your Mac.",
    "zh": "ThunderTalk：免费开源的 Mac 语音输入应用。在任意应用里按下快捷键说话，文字就出现在光标处，"
          "支持中英混说，识别在本机完成。",
}
_TEXTS = {
    ("en", True): _ABOUT["en"] + "\n{url}",
    ("zh", True): _ABOUT["zh"] + "\n{url}",
    ("en", False): _ABOUT["en"] + " Search for ThunderTalk on GitHub.",
    ("zh", False): _ABOUT["zh"] + "在 GitHub 搜 ThunderTalk 可以找到。",
}


def share_language(ui_language: str, system_languages: Sequence[str] = ()) -> str:
    """Chinese when the app is in Chinese or the Mac's first language is; English otherwise."""
    first = (system_languages[0] if system_languages else "").lower()
    return "zh" if ui_language == "zh" or first.startswith("zh") else "en"


def destination(language: str, dest_id: str) -> Destination:
    places = DESTINATIONS[language]
    return next((d for d in places if d.id == dest_id), places[0])


def share_text(language: str, dest_id: str) -> str:
    d = destination(language, dest_id)
    if d.id == "link":
        return SITE_URL
    return _TEXTS[(language, d.link)].format(url=SITE_URL)


def compose_url(language: str, dest_id: str, text: Optional[str] = None) -> str:
    """Where to open the place's composer with the text filled in ("" when it has none).
    ``text`` is what the user ended up with after editing; the ready text otherwise."""
    d = destination(language, dest_id)
    if not d.compose:
        return ""
    return d.compose.format(text=quote(share_text(language, dest_id) if text is None else text, safe=""))


def x_weight(text: str) -> int:
    """Length as X counts it: a URL is 23, CJK and other wide characters 2, the rest 1."""
    total = 0
    for word in text.replace("\n", " \n ").split(" "):
        if word.startswith(("http://", "https://")):
            total += 23
        else:
            total += sum(1 if ord(c) < 0x1100 else 2 for c in word)
        total += 1
    return total - 1


# ── what's new ────────────────────────────────────────────────────────────

# Version → i18n keys of at most three things an upgraded user would want to know.
# Add an entry in the release that ships them; versions without one show nothing.
WHATS_NEW: dict[str, tuple[str, ...]] = {
    "1.17.0": ("whatsnew.finish", "whatsnew.bar", "whatsnew.esc"),
    "1.18.0": ("whatsnew.voices", "whatsnew.polish", "whatsnew.loudness"),
    "1.19.0": ("whatsnew.queue",),
}


def _v(version: str) -> tuple[int, ...]:
    try:
        return tuple(int(p) for p in version.lstrip("vV").strip().split("."))
    except ValueError:
        return ()


def whats_new(current: str, seen: str, last_run: str) -> Optional[tuple[str, tuple[str, ...]]]:
    """(version, keys) to show after an update, or None.

    ``last_run`` is the version that ran before this launch ("" on a first
    install: a new user has just been through setup and is told nothing).
    ``seen`` is the version whose notes the user has already closed."""
    if not last_run or not _v(current):
        return None
    newer = [v for v in WHATS_NEW if _v(seen) < _v(v) <= _v(current)]
    if not newer:
        return None
    version = max(newer, key=_v)
    return version, WHATS_NEW[version][:3]


# ── which popup now ───────────────────────────────────────────────────────

def next_popup(*, current: str, seen: str, last_run: str, saved_secs: float, acknowledged_hours: int,
               off: bool, setup_done: bool, shown_this_launch: bool) -> Optional[tuple]:
    """("whatsnew", version, keys) | ("milestone", milestone, hours saved) | None.

    One popup per launch at most; the update note comes first, so someone who
    upgrades and has also passed a milestone hears about that on a later launch."""
    if shown_this_launch or not setup_done:
        return None
    note = whats_new(current, seen, last_run)
    if note is not None:
        return ("whatsnew", *note)
    due = milestone_due(saved_secs, acknowledged_hours, off)
    if due is not None:
        return ("milestone", due, int(saved_secs // 3600))
    return None
