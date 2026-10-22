"""Bilibili links resolve to the right video and part; API failures map to link error codes."""
import pytest

from thundertalk.core import bilibili


@pytest.mark.parametrize("url,bvid,part", [
    ("https://www.bilibili.com/list/watchlater?oid=117449840266004&bvid=BV1X6JX6sEYJ&spm_id_from=x&vd_source=y",
     "BV1X6JX6sEYJ", 1),
    ("https://www.bilibili.com/video/BV1X6JX6sEYJ/?spm_id_from=333.1007", "BV1X6JX6sEYJ", 1),
    ("https://www.bilibili.com/video/BV1X6JX6sEYJ?p=3", "BV1X6JX6sEYJ", 3),
    ("https://m.bilibili.com/video/BV1X6JX6sEYJ", "BV1X6JX6sEYJ", 1),
    ("https://www.bilibili.com/medialist/play/watchlater?bvid=BV1X6JX6sEYJ&p=2", "BV1X6JX6sEYJ", 2),
])
def test_ids_from_every_link_shape(url, bvid, part):
    assert bilibili.parse_ids(url) == (bvid, None, part)


def test_av_links_and_hosts():
    assert bilibili.parse_ids("https://www.bilibili.com/video/av170001?p=x") == (None, 170001, 1)
    assert bilibili.is_bilibili("https://b23.tv/abc123") and bilibili.is_bilibili("https://www.bilibili.com/x")
    assert not bilibili.is_bilibili("https://www.youtube.com/watch?v=bilibili")


@pytest.mark.parametrize("resp,code", [
    ({"code": -101, "message": "账号未登录"}, "login"),
    ({"code": -404, "message": "啥都木有"}, "unavailable"),
    ({"code": -412, "message": "请求被拦截"}, "blocked"),
    ({"code": 87008, "message": "当前视频为充电专属，请登录后观看"}, "login"),
])
def test_api_errors_become_link_codes(resp, code):
    with pytest.raises(bilibili.BilibiliError) as err:
        bilibili._check(resp, "view")
    assert err.value.code == code


def test_preview_only_videos_ask_for_login(monkeypatch, tmp_path):
    calls = []

    class Fake(bilibili._Client):
        def prime(self):
            self._mixin = "x" * 32

        def json(self, url):
            calls.append(url)
            if "/wbi/view" in url:
                return {"code": 0, "data": {"title": "T", "cid": 7, "duration": 60, "pages": []}}
            return {"code": 0, "data": {"is_preview": 1, "dash": {"audio": []}}}

    monkeypatch.setattr(bilibili, "_Client", Fake)
    with pytest.raises(bilibili.BilibiliError) as err:
        bilibili.fetch("https://www.bilibili.com/video/BV1X6JX6sEYJ", str(tmp_path))
    assert err.value.code == "login"
    assert all("w_rid=" in u and "wts=" in u for u in calls)


def test_signing_is_stable_for_the_same_second(monkeypatch):
    client = bilibili._Client()
    client._mixin = "0123456789abcdef0123456789abcdef"
    seen = []
    monkeypatch.setattr(client, "json", lambda url: seen.append(url) or {})
    monkeypatch.setattr(bilibili.time, "time", lambda: 1700000000)
    client.signed("https://api.bilibili.com/x/web-interface/wbi/view", {"bvid": "BV1X6JX6sEYJ", "x": "a(b)!"})
    q = seen[0].split("?", 1)[1]
    assert q.startswith("bvid=BV1X6JX6sEYJ&wts=1700000000&x=ab&w_rid=") and len(q.rsplit("=", 1)[1]) == 32
