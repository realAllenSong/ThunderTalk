"""Lyric check for composed takes: which lyric lines were sung clearly.

A take is recognised line by line (from the sentence timestamps the worker
saved) or, without timestamps, in overlapping windows whose text is spread over
the lines by global alignment. Chinese is compared on tone-less pinyin so a
homophone (宋 / 颂, or "song" heard for 宋) counts as sung correctly. The result
is advice: singing over a full mix is hard for a speech recogniser.
"""

from __future__ import annotations

import difflib
import functools
import json
import os
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Sequence

from thundertalk import asset_path
from thundertalk.core import tts_verify

SECTION_RE = re.compile(r"^\s*\[([^\]\n]+)\]\s*$")
PASS_ERROR = 0.35
PAD_S = 0.4
WINDOW_S = 15.0
OVERLAP_S = 2.0
MIN_SPAN_S = 0.3   # shorter lines mean the alignment collapsed (seen on real takes)

_PINYIN_LANGS = {"zh", "yue"}
_CHAR_LANGS = {"ja": "japanese", "ko": "korean"}
_CJK = re.compile(r"[\u3400-\u9fff\uf900-\ufaff]")

CLEAR, UNCLEAR, NOT_HEARD = "clear", "unclear", "not_heard"


@dataclass(frozen=True)
class LyricLine:
    index: int        # position among the sung lines (section tags and blank lines skipped)
    text: str
    section: str      # the [tag] above it, lower-cased; "" before the first tag


@dataclass(frozen=True)
class LineCheck:
    index: int
    heard: str
    status: str       # CLEAR | UNCLEAR | NOT_HEARD
    error: Optional[float]


def lyric_lines(lyrics: str) -> list[LyricLine]:
    out, section = [], ""
    for raw in lyrics.splitlines():
        if m := SECTION_RE.match(raw):
            section = m.group(1).strip().lower()
        elif raw.strip():
            out.append(LyricLine(len(out), raw.strip(), section))
    return out


def _pinyin(chars: str) -> list[str]:
    try:
        from pypinyin import lazy_pinyin
    except ImportError:             # scoring still works, homophones just count as errors
        return list(chars)
    return lazy_pinyin(chars)


def units(text: str, lang: str) -> list[str]:
    """Comparable units: tone-less pinyin syllables (and Latin words) for
    Chinese, characters for Japanese / Korean, words otherwise."""
    if lang in _PINYIN_LANGS:
        s = re.sub(r"[^\w\s]|_", " ", unicodedata.normalize("NFKC", text).lower())
        out: list[str] = []
        for run in re.findall(r"[\u3400-\u9fff\uf900-\ufaff]+|[a-z]+", s):
            out.extend(_pinyin(run) if _CJK.match(run) else [run])
        return out
    return tts_verify.tokens(text, _CHAR_LANGS.get(lang, "english"))


def error_rate(heard: Sequence[str], target: Sequence[str]) -> float:
    if not target:
        return 0.0
    return tts_verify.edit_distance(list(heard), list(target)) / len(target)


def check_line(index: int, heard: str, targets: Sequence[str], lang: str) -> LineCheck:
    """A line passes if it is close enough to any of `targets` (the sung text
    and the shown text)."""
    return judge(index, heard, units(heard, lang), [units(t, lang) for t in targets if t])


def judge(index: int, heard: str, got: Sequence[str], targets: Sequence[Sequence[str]]) -> LineCheck:
    if not got:
        return LineCheck(index, heard, NOT_HEARD, None)
    err = min((error_rate(got, t) for t in targets), default=0.0)
    return LineCheck(index, heard, CLEAR if err <= PASS_ERROR else UNCLEAR, round(err, 3))


def line_spans(sentences: Optional[Sequence[dict]], n_lines: int) -> Optional[list[tuple[float, float]]]:
    """(start, end) per lyric line from the worker's sentence timestamps,
    skipping section-tag sentences; None unless they match the lines one to one
    and every line has a plausible length."""
    if not sentences:
        return None
    spans = [(float(s["start"]), float(s["end"])) for s in sentences
             if str(s.get("text", "")).strip() and not SECTION_RE.match(str(s["text"]))]
    if len(spans) != n_lines or any(b - a < MIN_SPAN_S for a, b in spans):
        return None
    return spans


def line_windows(spans: Sequence[tuple[float, float]], duration: float,
                 pad: float = PAD_S) -> list[tuple[float, float]]:
    """Sentence timestamps padded on both sides and clamped to the take."""
    return [(max(0.0, a - pad), min(duration, b + pad)) for a, b in spans]


def scan_windows(duration: float, size: float = WINDOW_S,
                 overlap: float = OVERLAP_S) -> list[tuple[float, float]]:
    if duration <= 0:
        return []
    out, start, step = [], 0.0, size - overlap
    while True:
        end = min(duration, start + size)
        out.append((start, end))
        if end >= duration:
            return out
        start += step


def assign(heard: Sequence[str], lines: Sequence[Sequence[str]]) -> list[list[str]]:
    """Spread recognised units over the lyric lines by global alignment
    against the concatenated lines (fallback when there are no timestamps)."""
    owner = [i for i, line in enumerate(lines) for _ in line]
    out: list[list[str]] = [[] for _ in lines]
    if not owner:
        return out
    target = [u for line in lines for u in line]
    sm = difflib.SequenceMatcher(None, target, list(heard), autojunk=False)
    for _tag, i1, i2, j1, j2 in sm.get_opcodes():
        if j1 == j2:
            continue
        for k, j in enumerate(range(j1, j2)):
            # inserted units go to the line before the gap; replaced ones spread evenly
            pos = i1 + (k * (i2 - i1)) // (j2 - j1) if i2 > i1 else max(0, i1 - 1)
            out[owner[min(pos, len(owner) - 1)]].append(heard[j])
    return out


def overlap_merge(texts: Sequence[str], lang: str) -> list[str]:
    """Units of consecutive window transcripts with the repeated overlap
    removed (longest matching tail/head of up to 12 units)."""
    merged: list[str] = []
    for text in texts:
        cur = units(text, lang)
        best = 0
        for n in range(min(12, len(merged), len(cur)), 0, -1):
            if merged[-n:] == cur[:n]:
                best = n
                break
        merged.extend(cur[best:])
    return merged


def summary(checks: Sequence[LineCheck]) -> tuple[int, int]:
    """(clear lines, total lines) for the take badge."""
    return sum(c.status == CLEAR for c in checks), len(checks)


# ---- sung lyrics and the pronunciation helper ------------------------------
#
# A Chinese-singing model mis-sings Latin words ("欧拉SONG" → 神机); spelling
# them with characters ("欧拉宋") fixes it. Only the sung text goes to the
# model; the shown text is what the user wrote and what exports keep.

USER, BUILTIN, RULE = "user", "builtin", "rule"

_LATIN_WORD = re.compile(r"[A-Za-z]+(?:'[A-Za-z]+)?")

# Spelled-out letters for all-caps tokens (GPU → 吉屁优).
_LETTERS = dict(zip("ABCDEFGHIJKLMNOPQRSTUVWXYZ", (
    "诶 比 西 迪 伊 艾弗 吉 艾尺 艾 杰 开 艾勒 艾姆 恩 欧 屁 吉吾 阿 艾斯 替 优 维 达不溜 艾克斯 歪 贼"
).split()))

# Pinyin syllable → transliteration character, in the style of the Xinhua
# name-transliteration tables (so → suo → 索, song → 宋, la → 拉).
_SYLLABLES = dict(pair.split(":") for pair in """
a:阿 ai:艾 an:安 ang:昂 ao:奥 e:厄 ei:埃 en:恩 eng:恩 er:尔 o:奥 ou:欧 i:伊 in:因 ing:英
u:乌 un:温 ui:维 ong:翁 uo:沃 win:温
ba:巴 bai:拜 ban:班 bang:邦 bao:包 bei:贝 ben:本 beng:本 bi:比 bin:宾 bing:宾 bo:博 bu:布 bie:别
pa:帕 pai:派 pan:潘 pang:庞 pao:泡 pei:佩 pen:彭 peng:彭 pi:皮 pin:平 ping:平 po:波 pu:普 pou:剖
ma:马 mai:迈 man:曼 mang:芒 mao:毛 mei:梅 men:门 meng:蒙 mi:米 min:明 ming:明 mo:莫 mou:谋 mu:穆
fa:法 fan:凡 fang:方 fei:菲 fen:芬 feng:丰 fo:佛 fu:夫 fou:否
da:达 dai:代 dan:丹 dang:当 dao:道 de:德 dei:德 den:登 deng:登 di:迪 ding:丁 dou:斗 du:杜 duo:多
dong:东 dun:顿 dui:对
ta:塔 tai:泰 tan:坦 tang:唐 tao:陶 te:特 ten:滕 teng:滕 ti:蒂 ting:廷 tou:透 tu:图 tuo:托 tong:通
tun:吞 tui:退
na:纳 nai:奈 nan:南 nang:囊 nao:瑙 ne:内 nei:内 nen:嫩 neng:能 ni:尼 nin:宁 ning:宁 nou:诺 nu:努
nuo:诺 nong:农
la:拉 lai:莱 lan:兰 lang:朗 lao:劳 le:勒 lei:雷 len:伦 leng:棱 li:利 lin:林 ling:林 lou:娄 lu:卢
luo:洛 long:龙 lun:伦
ga:加 gai:盖 gan:甘 gang:冈 gao:高 ge:格 gei:盖 gen:根 geng:庚 gou:勾 gu:古 guo:果 gong:贡 gun:衮
gui:圭
ka:卡 kai:凯 kan:坎 kang:康 kao:考 ke:克 kei:凯 ken:肯 keng:肯 kou:寇 ku:库 kuo:阔 kong:孔 kun:昆
kui:奎
ha:哈 hai:海 han:汉 hang:杭 hao:豪 he:赫 hei:黑 hen:亨 heng:亨 hou:侯 hu:胡 huo:霍 hong:洪 hun:浑
hui:惠
ji:吉 jia:贾 jie:杰 jin:金 jing:京 jiu:久 ju:朱 jun:君 jiao:乔 jian:简 jiang:江
qi:奇 qia:恰 qie:切 qin:钦 qing:青 qiu:丘 qu:曲 qiao:乔 qian:钱 qiang:强
xi:西 xia:夏 xie:谢 xin:辛 xing:兴 xiu:修 xu:徐 xiao:肖 xian:先 xiang:香
zha:扎 zhai:宅 zhan:詹 zhang:章 zhao:赵 zhe:哲 zhen:珍 zheng:正 zhi:芝 zhou:周 zhu:朱 zhuo:卓
zhong:钟
cha:查 chai:柴 chan:钱 chang:昌 chao:乔 che:切 chen:陈 cheng:成 chi:奇 chou:丑 chu:楚 chuo:绰
chong:崇
sha:沙 shai:筛 shan:山 shang:尚 shao:邵 she:舍 shei:谁 shen:申 sheng:圣 shi:施 shou:寿 shu:舒
shuo:朔
za:扎 zai:宰 zan:赞 zang:藏 zao:藻 ze:泽 zei:贼 zen:曾 zeng:曾 zi:兹 zou:邹 zu:祖 zuo:佐 zong:宗
zun:尊
ca:擦 cai:蔡 can:灿 cang:仓 cao:曹 ce:策 cen:岑 ceng:曾 ci:茨 cou:凑 cu:粗 cuo:措 cong:丛 cun:村
sa:萨 sai:赛 san:桑 sang:桑 sao:骚 se:瑟 sen:森 seng:僧 si:斯 sou:叟 su:苏 suo:索 song:宋 sun:孙
sui:随
wa:瓦 wai:怀 wan:万 wang:王 wei:维 wi:维 we:韦 wen:文 weng:翁 wo:沃 wu:武 wong:翁
ya:亚 yan:延 yang:扬 yao:耀 ye:耶 yi:伊 yin:因 ying:英 you:尤 yu:宇 yong:永 yun:云 yue:约 yo:约
""".split())

# A consonant that has no vowel of its own (the s and t of "star", a final d).
_BARE = {"b": "布", "p": "普", "m": "姆", "f": "夫", "v": "夫", "d": "德", "t": "特", "n": "恩",
         "l": "尔", "r": "尔", "g": "格", "k": "克", "c": "克", "q": "克", "h": "赫", "j": "奇",
         "z": "兹", "s": "斯", "x": "克斯", "w": "乌", "y": "伊", "ch": "奇", "sh": "什",
         "th": "斯", "ph": "夫", "wh": "乌", "gh": "格", "ck": "克", "ng": "恩"}

_INITIAL = {"b": "b", "p": "p", "m": "m", "f": "f", "v": "w", "d": "d", "t": "t", "n": "n",
            "l": "l", "r": "l", "g": "g", "k": "k", "c": "k", "q": "k", "h": "h", "j": "j",
            "z": "z", "s": "s", "x": "s", "w": "w", "y": "y", "ch": "ch", "sh": "sh", "th": "s",
            "ph": "f", "wh": "w", "gh": "g", "ck": "k"}
_DIGRAPHS = ("ch", "sh", "th", "ph", "wh", "gh", "ck")

_FINAL = {"a": "a", "e": "e", "i": "i", "o": "o", "u": "u", "y": "i", "ee": "i", "ea": "i",
          "ie": "i", "oo": "u", "ou": "ou", "ai": "ai", "ay": "ai", "ei": "ei", "ey": "ei",
          "au": "ao", "oa": "ou", "oe": "ou", "ue": "u", "ui": "ui", "oy": "ai", "oi": "ai"}
_FINAL_N = {"a": "an", "e": "en", "i": "in", "o": "ong", "u": "un", "y": "in", "ee": "in",
            "ea": "in", "oo": "un"}
_FINAL_NG = {"a": "ang", "e": "eng", "i": "ing", "o": "ong", "u": "ong", "y": "ing"}

# onset consonants + vowel group + an optional n/ng coda that is not the next
# syllable's onset; a trailing cluster with no vowel is its own piece
_ORTHO_SYLLABLE = re.compile(
    r"((?:y(?=[aeiou])|[^aeiouy])*)([aeiouy]+)(ng(?![aeiouy])|n(?![aeiouy]))?|([^aeiouy]+)$")


@dataclass(frozen=True)
class Suggestion:
    text: str
    source: str       # USER | BUILTIN | RULE ("suggested — listen and adjust")


def user_dict_path() -> Path:
    return Path.home() / ".thundertalk" / "music" / "pronunciations.json"


def load_user_dict(path: Optional[Path] = None) -> dict[str, str]:
    try:
        data = json.loads((path or user_dict_path()).read_text("utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(k).lower(): str(v) for k, v in data.items() if str(v).strip()}


def remember(word: str, sung: str, path: Optional[Path] = None) -> None:
    """Save the user's sung form for `word` (an empty `sung` forgets it)."""
    path = path or user_dict_path()
    data = load_user_dict(path)
    if sung.strip():
        data[word.lower()] = sung.strip()
    else:
        data.pop(word.lower(), None)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1, sort_keys=True), "utf-8")
    os.replace(tmp, path)


@functools.lru_cache(maxsize=1)
def builtin_dict() -> dict[str, str]:
    out: dict[str, str] = {}
    try:
        with open(asset_path(os.path.join("music", "sung_zh.tsv")), encoding="utf-8") as fh:
            for line in fh:
                word, sep, sung = line.rstrip("\n").partition("\t")
                if sep and not word.startswith("#") and sung.strip():
                    out[word.strip().lower()] = sung.strip()
    except OSError:
        pass
    return out


def _split_onset(onset: str) -> list[str]:
    """Consonant onset into sounds: digraphs kept, doubled letters merged
    (str → s, t, r; ll → l)."""
    out, i = [], 0
    while i < len(onset):
        two = onset[i:i + 2]
        if two in _DIGRAPHS:
            out.append(two)
            i += 2
            continue
        if not out or out[-1] != onset[i]:
            out.append(onset[i])
        i += 1
    return out


def _syllable_char(initial: str, final: str) -> str:
    if final == "o" and initial not in ("", "b", "p", "m", "f", "w", "y"):
        final = "uo"
    for cand in (initial + final, initial + final.rstrip("g"), initial + final[:1],
                 initial + final[:2]):
        if cand in _SYLLABLES:
            return _SYLLABLES[cand]
    bare = next((k for k, v in _INITIAL.items() if v == initial), "")
    head = _BARE.get(bare, "")
    return head + _SYLLABLES.get(final, "") if head or final in _SYLLABLES else ""


def transliterate(word: str) -> str:
    """Rule-based sung form for a Latin word: all-caps spelled letter by
    letter, anything else syllable by syllable."""
    if len(word) > 1 and word.isupper() and word.isalpha():
        return "".join(_LETTERS.get(c, c) for c in word)
    w = re.sub(r"[^a-z]", "", word.lower())
    out = []
    for m in _ORTHO_SYLLABLE.finditer(w):
        onset, vowels, coda, tail = m.groups()
        if tail:
            for c in _split_onset(tail):
                if (ch := _BARE.get(c, "")) and not (out and out[-1] == ch):
                    out.append(ch)          # "rld" → 尔德, not 尔尔德
            continue
        parts = _split_onset(onset)
        out.extend(_BARE.get(c, "") for c in parts[:-1])
        first = parts[-1] if parts else ""
        if first == "c" and vowels[:1] in ("e", "i", "y"):
            first = "s"
        table = {"ng": _FINAL_NG, "n": _FINAL_N}.get(coda or "", _FINAL)
        final = table.get(vowels) or table.get(vowels[:1]) or _FINAL.get(vowels[:1], "a")
        out.append(_syllable_char(_INITIAL.get(first, ""), final))
    return "".join(out) or word


def suggest(word: str, context_lang: str = "zh", user: Optional[dict[str, str]] = None) -> Suggestion:
    """How to spell `word` so it is sung right in a `context_lang` song: the
    user's own choice, else the built-in table, else the transliteration rules.
    Words in songs that are not sung in Chinese are left alone."""
    key = word.lower()
    user = load_user_dict() if user is None else user
    if key in user:
        return Suggestion(user[key], USER)
    if context_lang not in _PINYIN_LANGS:
        return Suggestion(word, RULE)
    if key in builtin_dict():
        return Suggestion(builtin_dict()[key], BUILTIN)
    return Suggestion(transliterate(word), RULE)


def suggest_sung(word: str, context_lang: str = "zh", user: Optional[dict[str, str]] = None) -> str:
    return suggest(word, context_lang, user).text


def latin_words(line: str) -> list[str]:
    """The Latin words of a line that also has Chinese in it (the ones a
    Chinese-singing model is likely to mis-sing); [] for other lines."""
    return _LATIN_WORD.findall(line) if _CJK.search(line) else []


def sung_line(shown: str, context_lang: str = "zh", user: Optional[dict[str, str]] = None) -> str:
    """The shown line with each Latin word replaced by its suggested sung
    form (欧拉SONG → 欧拉宋); lines without Chinese are kept as written."""
    if context_lang not in _PINYIN_LANGS or not _CJK.search(shown):
        return shown
    user = load_user_dict() if user is None else user
    return _LATIN_WORD.sub(lambda m: suggest_sung(m.group(0), context_lang, user), shown)


def sung_pairs(lyrics: str, context_lang: str, overrides: Optional[dict[str, str]] = None,
               user: Optional[dict[str, str]] = None) -> list[tuple[LyricLine, str]]:
    """(shown line, sung text) per lyric line. `overrides` maps a shown line's
    text to the sung text the user typed, so it survives edits elsewhere."""
    overrides = overrides or {}
    user = load_user_dict() if user is None else user
    return [(ln, overrides.get(ln.text) or sung_line(ln.text, context_lang, user))
            for ln in lyric_lines(lyrics)]


def sung_lyrics(lyrics: str, context_lang: str, overrides: Optional[dict[str, str]] = None,
                user: Optional[dict[str, str]] = None) -> str:
    """The lyrics sent to the model: section tags and blank lines kept, every
    lyric line replaced by its sung text."""
    sung = iter(s for _, s in sung_pairs(lyrics, context_lang, overrides, user))
    out = []
    for raw in lyrics.splitlines():
        out.append(raw if SECTION_RE.match(raw) or not raw.strip() else next(sung))
    return "\n".join(out)
