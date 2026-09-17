from pathlib import Path

from yt_dlp.downloader.streaming.dash import DashOutput


def test_dash_output_writes_manifest_and_segments(tmp_path):
    output = DashOutput(tmp_path)
    video = {
        '_streaming_stream_index': 0,
        '_streaming_info': {
            'vcodec': 'avc1.64001f',
            'width': 1280,
            'height': 720,
            'tbr': 1000,
        },
    }
    audio = {
        '_streaming_stream_index': 1,
        '_streaming_info': {
            'vcodec': 'none',
            'acodec': 'mp4a.40.2',
            'tbr': 128,
        },
    }

    output.write_fragment({**video, 'duration': None}, b'video-init')
    output.write_fragment({**audio, 'duration': None}, b'audio-init')
    output.write_fragment({**video, 'duration': 2}, b'video-segment')
    output.write_fragment({**audio, 'duration': 2}, b'audio-segment')
    output.finalize()

    assert (tmp_path / 'init-0.mp4').read_bytes() == b'video-init'
    assert (tmp_path / 'init-1.mp4').read_bytes() == b'audio-init'
    assert (tmp_path / 'chunk-0-1.m4s').read_bytes() == b'video-segment'
    assert (tmp_path / 'chunk-1-1.m4s').read_bytes() == b'audio-segment'

    manifest = (tmp_path / 'manifest.mpd').read_text(encoding='utf-8')
    assert 'type="static"' in manifest
    assert 'contentType="video"' in manifest
    assert 'contentType="audio"' in manifest
    assert 'chunk-0-$Number$.m4s' in manifest
    assert 'chunk-1-$Number$.m4s' in manifest


def test_dash_output_updates_manifest_atomically(tmp_path):
    output = DashOutput(Path(tmp_path))
    fragment = {
        '_streaming_stream_index': 0,
        '_streaming_info': {'vcodec': 'vp9', 'tbr': 500},
        'duration': 1,
    }

    output.write_fragment(fragment, b'segment')

    assert (tmp_path / 'manifest.mpd').exists()
    assert not (tmp_path / 'manifest.mpd.tmp').exists()
