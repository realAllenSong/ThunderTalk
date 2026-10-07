"""Music model catalog, hardware tiers and takes folders."""

from __future__ import annotations

import pytest

from thundertalk.core import model_metadata, models, music, speech


def test_catalog_downloads_are_code_free_and_land_where_acestep_looks():
    for m in music.MUSIC_MODELS:
        assert m.info.components == ("torch", "music")
        assert m.info.language_codes == model_metadata.ACESTEP_LANGUAGES
        for d in m.info.downloads + (music.PLANNER,):
            assert d.kind == "hf-dir" and d.dest.startswith(music.CHECKPOINTS) and d.allow
            assert not any(g.endswith(".py") or g in ("*", "**") for g in d.allow)
    assert any(g.startswith("acestep-v15-turbo/") for g in music.TURBO.info.downloads[0].allow)
    assert music.XL.info.downloads[0].dest.endswith("/" + music.XL.folder)
    assert music.XL.convert_bf16 and not music.TURBO.convert_bf16
    assert (music.XL.steps, music.TURBO.steps) == (50, 8)
    assert music.with_planner(music.TURBO).downloads[-1] is music.PLANNER
    assert music.model("acestep-turbo") is music.TURBO
    with pytest.raises(KeyError):
        music.model("nope")


def test_shared_checkpoint_folder_tracks_each_download(isolated_home, monkeypatch):
    def snapshot(repo, local_dir, allow, ignore, cb, cancel):
        (local_dir / allow[0].split("/")[0]).mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(models, "_hf_snapshot", snapshot)
    monkeypatch.setattr("thundertalk.core.runtime.needed", lambda *a, **k: False)
    speech._download_hf_dir(music.VAE, lambda *a: None, None)
    assert speech.download_ready(music.VAE)
    assert not speech.download_ready(music.TEXT_ENCODER)
    assert not speech.download_ready(music.TURBO.info.downloads[0])


@pytest.mark.parametrize("gb, offered, rec, planner, takes, max_s, backend", [
    (16, ("acestep-turbo",), "acestep-turbo", False, 1, 240, "mps"),
    (18, ("acestep-turbo",), "acestep-turbo", False, 1, 240, "mps"),
    (24, ("acestep-turbo",), "acestep-turbo", True, 2, 600, "mlx"),
    (36, ("acestep-xl", "acestep-turbo"), "acestep-xl", True, 2, 600, "mlx"),
    (128, ("acestep-xl", "acestep-turbo"), "acestep-xl", True, 2, 600, "mlx"),
])
def test_tiers(gb, offered, rec, planner, takes, max_s, backend):
    t = music.tier(gb)
    assert (t.models, t.recommended, t.planner, t.takes, t.max_seconds, t.dit_backend) == \
        (offered, rec, planner, takes, max_s, backend)
    assert t.experimental == (gb < 24)
    assert ("acestep-xl" in t.disabled) == (gb < 32)


def test_compose_hidden_below_16_gb():
    assert music.tier(8) is None and music.tier(15.0) is None
    assert music.tier(15.99) is not None


def test_xl_length_defaults_to_300_s_with_600_selectable():
    t = music.tier(36)
    assert t.default_max_seconds("acestep-xl") == 300
    assert t.default_max_seconds("acestep-turbo") == 600
    assert music.clamp_seconds(900, t) == 600 and music.clamp_seconds(3, t) == 10
    assert music.clamp_seconds(900, music.tier(16)) == 240


def test_planner_pre_ticked_needs_32_gb_and_20_gb_disk_left():
    assert music.planner_pre_ticked(36, disk_free_mb=60_000, pending_mb=30_000)
    assert not music.planner_pre_ticked(36, disk_free_mb=45_000, pending_mb=30_000)
    assert not music.planner_pre_ticked(24, disk_free_mb=500_000, pending_mb=10_000)
    assert music.ram_need_mb(music.TURBO, True) == music.TURBO.ram_mb + music.PLANNER_RAM_MB


def test_job_dirs_live_in_music_folder_and_never_collide(isolated_home):
    a = music.new_job_dir("Warm indie pop, acoustic guitar!", now=0)
    b = music.new_job_dir("Warm indie pop, acoustic guitar!", now=0)
    assert a.parent == isolated_home / "Music" / "ThunderTalk"
    assert a.name.endswith("-warm-indie-pop-acoustic-guitar") and b.name == a.name + "-2"
    assert music.slug("夏夜 晚风") == "夏夜-晚风" and music.slug("!!!") == "song"
