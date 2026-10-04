"""Web links (yt-dlp mocked), subtitle cues, batch saving and subtitle burn-in."""

from __future__ import annotations

import os
import threading
from pathlib import Path

import numpy as np
import pytest

from thundertalk.core import audio_io, burn, links
from thundertalk.core import transcribe as tr

SR = 16000


def _speech_wav(path: Path, seconds: float = 3.0) -> None:
    t = np.arange(int(seconds * SR)) / SR
    env = 0.04 + 0.96 * np.sin(2 * np.pi * 3.0 * t) ** 2
    audio_io.write_wav(str(path), (0.2 * env * np.sin(2 * np.pi * 150 * t)).astype(np.float32), SR)


class FakeYDL:
    """Stands in for yt_dlp.YoutubeDL. Class attributes script its behaviour."""
    errors: list[str] = []            # raised by extract_info, one per call, before succeeding
    info: dict = {}
    calls = 0
    opts_seen: list[dict] = []

    def __init__(self, opts):
        self.opts = opts
        FakeYDL.opts_seen.append(opts)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def extract_info(self, url, download=False):
        from yt_dlp.utils import DownloadError
        FakeYDL.calls += 1
        if FakeYDL.errors:
            raise DownloadError(FakeYDL.errors.pop(0))
        return dict({"id": "abc123", "title": "My Talk: part 1/2", "duration": 3.0, "ext": "wav"}, **FakeYDL.info)

    def process_ie_result(self, info, download=True):
        for k in range(1, 5):
            for hook in self.opts["progress_hooks"]:
                hook({"status": "downloading", "downloaded_bytes": k * 100, "total_bytes": 400})
        path = Path(self.opts["outtmpl"].replace("%(id)s", info["id"]).replace("%(ext)s", "wav"))
        _speech_wav(path)
        return dict(info, requested_downloads=[{"filepath": str(path)}])


@pytest.fixture
def fake_ytdlp(monkeypatch, tmp_path):
    import yt_dlp
    FakeYDL.errors, FakeYDL.info, FakeYDL.calls, FakeYDL.opts_seen = [], {}, 0, []
    monkeypatch.setattr(yt_dlp, "YoutubeDL", FakeYDL)
    monkeypatch.setattr(links.time, "sleep", lambda s: None)
    made = []
    real = links.tempfile.mkdtemp

    def mkdtemp(prefix=""):
        d = real(prefix=prefix, dir=str(tmp_path))
        made.append(d)
        return d

    monkeypatch.setattr(links.tempfile, "mkdtemp", mkdtemp)
    FakeYDL.made = made
    return FakeYDL


# ── recognising links ────────────────────────────────────────────────────

def test_find_urls_in_share_text():
    share = "【一分钟看懂-哔哩哔哩】 https://b23.tv/AbCdEf 复制链接"
    assert links.find_urls(share) == ["https://b23.tv/AbCdEf"]
    assert links.find_urls("youtu.be/jNQXAC9IVRw") == ["https://youtu.be/jNQXAC9IVRw"]
    two = "https://www.youtube.com/watch?v=a1, https://www.bilibili.com/video/BV1x."
    assert links.find_urls(two) == ["https://www.youtube.com/watch?v=a1", "https://www.bilibili.com/video/BV1x"]
    assert links.find_urls("just some words") == [] and not links.is_url("meeting.m4a")
    assert links.site_name("https://m.bilibili.com/video/BV1") == "bilibili.com"


def test_safe_name():
    assert links.safe_name('A/B: "c"?') == "A B c"
    assert links.safe_name("   ") == "transcript"
    assert len(links.safe_name("x" * 300)) == 80


@pytest.mark.parametrize("msg, code", [
    ("ERROR: [youtube] abc: Private video. Sign in if you've been granted access to this video", "private"),
    ("ERROR: [youtube] abc: Sign in to confirm you’re not a bot. Use --cookies-from-browser", "login"),
    ("ERROR: [youtube] abc: Sign in to confirm your age", "login"),
    ("ERROR: [BiliBili] 1x: This video may be deleted or geo-restricted.", "unavailable"),
    ("ERROR: [youtube] abc: The uploader has not made this video available in your country", "geo"),
    ("ERROR: Unable to download webpage: HTTP Error 412: Precondition Failed", "blocked"),
    ("ERROR: Unable to download webpage: <urlopen error [Errno 8] nodename nor servname provided>", "network"),
    ("ERROR: Unsupported URL: https://example.com/", "unsupported"),
    ("ERROR: [youtube] abc: Video unavailable", "unavailable"),
    ("ERROR: [BiliBili] BV1t: Requested format is not available. Use --list-formats", "no_audio"),
    ("ERROR: something new and strange", "other"),
])
def test_error_classification(msg, code):
    assert links.classify(msg) == code


# ── fetching (yt-dlp mocked) ─────────────────────────────────────────────

def test_fetch_audio_reports_title_progress_and_prefers_m4a(fake_ytdlp):
    seen, titles = [], []
    got = links.fetch_audio("https://youtu.be/x", lambda p, d, t: seen.append(p), on_title=titles.append)
    assert titles == ["My Talk: part 1/2"] and got.title == "My Talk: part 1/2"
    assert seen == [25, 50, 75, 100]
    assert os.path.isfile(got.path) and Path(got.path).parent == Path(got.workdir)
    opts = fake_ytdlp.opts_seen[0]
    assert opts["format"].startswith("bestaudio[ext=m4a]") and opts["noplaylist"] and not opts["postprocessors"]


def test_fetch_errors_are_friendly_and_leave_nothing_behind(fake_ytdlp):
    fake_ytdlp.errors = ["ERROR: [youtube] abc: Private video. Sign in if you've been granted access"]
    with pytest.raises(links.LinkError) as ei:
        links.fetch_audio("https://youtu.be/x")
    assert ei.value.code == "private" and "Private video" in ei.value.detail
    assert all(not os.path.exists(d) for d in fake_ytdlp.made)


def test_bilibili_412_is_retried_once(fake_ytdlp):
    fake_ytdlp.errors = ["ERROR: [BiliBili] BV1: Unable to download JSON metadata: HTTP Error 412: Precondition Failed"]
    got = links.fetch_audio("https://www.bilibili.com/video/BV1")
    assert fake_ytdlp.calls == 2 and got.title
    fake_ytdlp.errors = ["HTTP Error 412: Precondition Failed"] * 2
    with pytest.raises(links.LinkError) as ei:
        links.fetch_audio("https://www.bilibili.com/video/BV1")
    assert ei.value.code == "blocked"


def test_playlists_and_live_streams_are_refused(fake_ytdlp):
    fake_ytdlp.info = {"_type": "playlist", "entries": []}
    with pytest.raises(links.LinkError) as ei:
        links.fetch_audio("https://www.youtube.com/playlist?list=x")
    assert ei.value.code == "playlist"
    fake_ytdlp.info = {"is_live": True}
    with pytest.raises(links.LinkError) as ei:
        links.fetch_audio("https://www.youtube.com/watch?v=live")
    assert ei.value.code == "live"


def test_cancel_during_download_cleans_up(fake_ytdlp):
    ev = threading.Event()

    def prog(p, d, t):
        if p >= 50:
            ev.set()

    with pytest.raises(links.LinkCancelled):
        links.fetch_audio("https://youtu.be/x", prog, cancel=ev)
    assert all(not os.path.exists(d) for d in fake_ytdlp.made)


class FakeAsr:
    is_loaded = True
    current_model = "Fake-ASR"

    def recognize(self, x, sr, **kw):
        from types import SimpleNamespace
        return SimpleNamespace(text="hello there")


def test_transcribe_link_titles_the_transcript_and_deletes_the_download(fake_ytdlp):
    msgs = []
    t = tr.transcribe_link("https://youtu.be/x", FakeAsr(), progress=lambda p, m: msgs.append(m))
    assert t.title == "My Talk: part 1/2" and t.source_url == "https://youtu.be/x"
    assert msgs[0] == "fetch" and "download:400:400" in msgs and "decode" in msgs
    assert all(not os.path.exists(d) for d in fake_ytdlp.made)
    assert t.to_markdown().startswith("# My Talk: part 1/2")
    assert '"title": "My Talk: part 1/2"' in t.to_json()
    assert not t.is_partial


def test_a_preview_shorter_than_advertised_is_flagged(fake_ytdlp):
    fake_ytdlp.info = {"duration": 525.0}          # the site served a 3 s "preview" of a long video
    t = tr.transcribe_link("https://www.bilibili.com/video/BV1", FakeAsr())
    assert t.expected_duration == 525.0 and t.is_partial


def test_cleanup_stale(tmp_path, monkeypatch):
    monkeypatch.setattr(links.tempfile, "gettempdir", lambda: str(tmp_path))
    old, new = tmp_path / (links.TEMP_PREFIX + "old"), tmp_path / (links.TEMP_PREFIX + "new")
    old.mkdir()
    new.mkdir()
    os.utime(old, (1, 1))
    assert links.cleanup_stale() == 1 and not old.exists() and new.exists()


# ── saving and cues ──────────────────────────────────────────────────────

def test_save_outputs_never_overwrites(tmp_path):
    t = tr.Transcript([tr.Segment(0, 2, "Hello.")], 2.0, "Fake")
    a = tr.save_outputs(t, str(tmp_path), "talk", ["txt", "srt"])
    b = tr.save_outputs(t, str(tmp_path), "talk", ["txt"])
    assert [Path(p).name for p in a] == ["talk.txt", "talk.srt"] and Path(b[0]).name == "talk (2).txt"


def test_subtitle_cues_split_long_segments_by_clause_and_time():
    zh = "大家好，欢迎来到雷语工作室。今天我们来测试一下把字幕直接烧录进视频里面的效果，看看长句子会不会自动换行。"
    en = ("And this is an English sentence that is long enough to need wrapping onto a second line "
          "on screen and then some more words to go past the limit")
    t = tr.Transcript([tr.Segment(0, 9, zh, "S01"), tr.Segment(10, 16, en, "S01")], 16, "x", has_speakers=True)
    cues = tr.subtitle_cues(t)
    assert len(cues) >= 4 and all(len(c.text) <= 84 + 6 for c in cues)
    assert cues[0].text.startswith("S01: ") and not any(c.text.startswith("S01") for c in cues[1:])
    zh_cues = [c for c in cues if c.end <= 9.0001]
    assert zh_cues[0].start == 0 and zh_cues[-1].end == pytest.approx(9.0)
    assert "".join(c.text for c in zh_cues).replace("S01: ", "") == zh
    en_cues = [c for c in cues if c.start >= 10]
    assert max(len(c.text) for c in en_cues) - min(len(c.text) for c in en_cues) < 30   # balanced, not 84 + 6
    srt = tr.cues_to_srt(cues)
    assert srt.startswith("1\n00:00:00,000 --> ")


# ── burn-in ──────────────────────────────────────────────────────────────

FFMPEG_I = """Input #0, mov,mp4,m4a,3gp,3g2,mj2, from 'IMG_0001.MOV':
  Duration: 00:01:02.50, start: 0.000000, bitrate: 9000 kb/s
  Stream #0:0[0x1](und): Video: hevc (Main) (hvc1 / 0x31637668), yuv420p(tv), 1920x1080, 8000 kb/s, 30 fps
      Side data:
        displaymatrix: rotation of -90.00 degrees
  Stream #0:1[0x2](und): Audio: aac (LC) (mp4a / 0x6134706D), 44100 Hz, mono, fltp, 96 kb/s
"""
COVER_ONLY = """  Duration: 00:03:00.00, start: 0.0
  Stream #0:0: Audio: mp3, 44100 Hz, stereo, fltp, 128 kb/s
  Stream #0:1: Video: mjpeg (Baseline), yuvj420p(pc), 600x600 [SAR 1:1 DAR 1:1], 90k tbr (attached pic)
"""


def test_parse_probe_handles_rotation_and_cover_art():
    info = burn.parse_probe(FFMPEG_I)
    assert (info.width, info.height) == (1080, 1920) and info.duration == pytest.approx(62.5)
    assert info.has_audio and info.audio_codec == "aac"
    assert burn.parse_probe(COVER_ONLY) is None


def test_build_command_hard_and_soft():
    info = burn.VideoInfo(1280, 720, 10.0, True, "aac")
    cmd = burn.build_command("ffmpeg", "/v/in.mp4", "cues.ffconcat", "/v/out.mp4", info, "libx264",
                             soft_srt="subs.srt", language="chi")
    s = " ".join(cmd)
    assert cmd[cmd.index("-i") + 1] == "/v/in.mp4"
    assert "overlay" not in s and "-map 0:v:0 -map 0:a?" in s
    assert "-map 1:s" in s and "-c:s mov_text -metadata:s:s:0 language=chi" in s
    assert "-c:v copy" in s and "-c:a copy" in s and cmd[-1] == "/v/out.mp4"
    assert "-progress pipe:1" in s
    hard = burn.build_command("ffmpeg", "/v/in.webm", "c", "/v/out.mp4", burn.VideoInfo(640, 360, 5, True, "opus"),
                              "h264_videotoolbox")
    s = " ".join(hard)
    assert "overlay=x=(W-w)/2:y=H-h" in s
    assert "mov_text" not in s and "-c:a aac" in s and "h264_videotoolbox" in s
    silent = burn.build_command("ffmpeg", "a.mov", "c", "b.mov", burn.VideoInfo(640, 360, 5, False), "libx264")
    assert "0:a?" not in silent and "-c:a" not in silent


def test_concat_list_holds_each_cue_for_its_time():
    cues = [tr.Segment(1.0, 3.0, "a"), tr.Segment(2.5, 4.0, "b"), tr.Segment(9.5, 12.0, "c")]
    text = burn.concat_list(cues, ["c0.png", "c1.png", "c2.png"], "blank.png", 10.0)
    rows = text.splitlines()
    assert rows[0] == "ffconcat version 1.0" and rows[-1] == "file 'blank.png'"
    pairs = list(zip(rows[1::2], rows[2::2]))
    assert pairs[:3] == [("file 'blank.png'", "duration 1.000"), ("file 'c0.png'", "duration 2.000"),
                         ("file 'c1.png'", "duration 1.000")]             # overlap pushed after the previous cue
    total = sum(float(d.split()[1]) for _, d in pairs)
    assert total == pytest.approx(10.04, abs=0.01)                        # clipped to the video length


def test_progress_lines_and_defaults():
    assert burn.parse_progress_time("out_time_us=2500000") == 2.5
    assert burn.parse_progress_time("out_time_ms=1000000") == 1.0
    assert burn.parse_progress_time("out_time_us=N/A") is None and burn.parse_progress_time("frame=10") is None
    assert burn.default_output("/v/clip.webm") == "/v/clip (subtitled).mp4"
    assert burn.default_output("/v/clip.mov") == "/v/clip (subtitled).mov"
    assert burn.is_video("/x/a.mkv") and not burn.is_video("/x/a.m4a")


def test_wrap_text_latin_and_cjk():
    width = lambda s: float(len(s))                      # noqa: E731
    assert burn.wrap_text("one two three four", width, 9) == ["one two", "three", "four"]
    assert burn.wrap_text("一二三四五六七", width, 3) == ["一二三", "四五六", "七"]


def test_renderer_draws_white_text_with_dark_outline(qapp, tmp_path):
    from PySide6.QtGui import QImage
    r = burn.CueRenderer(640, 360)
    p = tmp_path / "c.png"
    r.render("你好 Hello", str(p))
    img = QImage(str(p))
    assert (img.width(), img.height()) == (640, r.band_h) and r.band_h % 2 == 0
    px = [img.pixelColor(x, y) for x in range(0, 640, 2) for y in range(0, r.band_h, 2)]
    assert any(c.alpha() == 255 and c.red() > 240 for c in px)            # white fill
    assert any(c.alpha() > 200 and c.red() < 30 for c in px)              # dark outline
    assert any(165 <= c.alpha() <= 175 and c.red() < 10 for c in px)  # 67% box
    assert img.pixelColor(2, 2).alpha() == 0                              # transparent elsewhere


@pytest.mark.skipif(audio_io.find_ffmpeg() is None, reason="needs ffmpeg")
def test_real_burn_with_soft_track_and_cancel(qapp, tmp_path):
    import subprocess
    ff = audio_io.find_ffmpeg()
    src, out = tmp_path / "clip.mp4", tmp_path / "out.mp4"
    subprocess.run([ff, "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=25", "-f", "lavfi",
                    "-i", "sine=frequency=300", "-t", "3", "-pix_fmt", "yuv420p", "-c:a", "aac", str(src)], check=True)
    t = tr.Transcript([tr.Segment(0.2, 1.4, "第一句字幕"), tr.Segment(1.5, 2.8, "Second line")], 3.0, "x")
    pcts = []
    r = burn.burn(str(src), t, str(out), soft=True, progress=lambda p, m: pcts.append((p, m)))
    assert r.cues == 2 and out.is_file() and pcts[-1] == (100, "done")
    probe = subprocess.run([ff, "-hide_banner", "-i", str(out)], capture_output=True, text=True).stderr
    assert "mov_text" in probe and "320x180" in probe and "Audio: aac" in probe
    ev = threading.Event()
    ev.set()
    with pytest.raises(burn.BurnCancelled):
        burn.burn(str(src), t, str(tmp_path / "never.mp4"), cancel=ev)
    assert not (tmp_path / "never.mp4").exists()


def test_burn_without_ffmpeg_says_so(tmp_path, monkeypatch):
    monkeypatch.setattr(audio_io, "find_ffmpeg", lambda: None)
    with pytest.raises(burn.BurnError) as ei:
        burn.burn(str(tmp_path / "a.mp4"), tr.Transcript([], 0, "x"), str(tmp_path / "b.mp4"))
    assert ei.value.code == "no_ffmpeg"


def test_portrait_cues_fit_large_font_without_losing_words(qapp):
    renderer = burn.CueRenderer(720, 1280)
    text = "这是需要在竖屏视频中显示的很长的一段中文字幕。" * 4
    cues = renderer.fit_cues([tr.Segment(0, 12, text)])
    assert len(cues) > 1
    assert "".join(c.text.replace("\n", "") for c in cues) == text
    assert cues[0].start == 0 and cues[-1].end == pytest.approx(12)
    assert all(len(renderer.lines(c.text)) <= renderer.style.max_lines for c in cues)
    assert all(renderer.metrics.horizontalAdvance(line) <= renderer.w * .9
               for c in cues for line in renderer.lines(c.text))
    assert burn.wrap_text("abcdefghijk", len, 4) == ["abcd", "efgh", "ijk"]
