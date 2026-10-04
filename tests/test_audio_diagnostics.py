"""Private, bounded diagnostics cannot interfere with speaker restoration."""
import json
import stat

from thundertalk.core import audio_diagnostics as diag
from thundertalk.core import system_audio as audio


def test_rotation_caps_disk_usage_and_keeps_private_permissions(isolated_home, monkeypatch):
    monkeypatch.setattr(diag, '_logger', None)
    try:
        for n in range(1800):
            diag.audio_diagnostic('mute_write', generation=n, device_id=113,
                                  element='mute:0', target=True, accepted=True,
                                  readback={'mute:0': True, 'volm:1': .3125, 'volm:2': .3125})
        files = list((isolated_home / '.thundertalk' / 'logs').glob('audio.log*'))
        assert len(files) == 3
        assert sum(p.stat().st_size for p in files) <= 3 * 96 * 1024
        assert all(stat.S_IMODE(p.stat().st_mode) == 0o600 for p in files)
        assert all(json.loads(line[line.index('{'):])['event'] == 'mute_write'
                   for p in files for line in p.read_text().splitlines())
    finally:
        for handler in diag._get_logger().handlers:
            handler.close()


def test_log_failure_never_blocks_audio_cleanup(monkeypatch):
    def fail():
        raise OSError('disk full')
    monkeypatch.setattr(diag, '_get_logger', fail)
    diag.audio_diagnostic('restore_write', target=False)


def test_controller_logs_numeric_device_id_without_uid_or_name(tmp_path, _isolated_audio_log):
    class Backend:
        def devices(self):
            return {'private-name-and-hardware-serial': 113}
    controller = audio._DuckingController(Backend(), tmp_path / 'state.json')
    controller._diag('restore_readback', 'private-name-and-hardware-serial',
                     current={'mute:0': False, 'volm:1': .3125})
    contents = _isolated_audio_log.getvalue()
    assert 'private-name' not in contents
    assert 'serial' not in contents
    assert '113' in contents
