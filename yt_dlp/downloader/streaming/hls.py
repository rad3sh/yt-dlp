import os
import threading
from pathlib import Path


class HlsOutput:
    """Write downloaded HLS media fragments and a progressively updated playlist."""

    def __init__(self, directory, logger=None, stream_index=None, info_dict=None, master_state=None):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.logger = logger
        self.stream_index = stream_index
        self.info_dict = info_dict or {}
        self.master_state = master_state
        self.track_directory = self.directory / (
            'audio' if stream_index == 1 else 'video') if stream_index is not None else self.directory
        self.track_directory.mkdir(parents=True, exist_ok=True)
        self._segments = []
        self._init_name = None
        self._lock = threading.Lock()

    def write_fragment(self, fragment, data):
        with self._lock:
            if fragment.get('is_init_segment'):
                self._init_name = 'init.mp4'
                (self.track_directory / self._init_name).write_bytes(data)
                self._write_playlist()
                return
            duration = fragment.get('duration') or 2.0
            number = len(self._segments) + 1
            name = f'segment-{number:05d}.ts'
            (self.track_directory / name).write_bytes(data)
            self._segments.append((name, float(duration)))
            self._write_playlist()

    def finalize(self):
        with self._lock:
            self._write_playlist(endlist=True)

    def _write_playlist(self, endlist=False):
        if not self._segments:
            return
        target_duration = max(1, max(duration for _, duration in self._segments))
        lines = [
            '#EXTM3U', '#EXT-X-VERSION:3',
            f'#EXT-X-TARGETDURATION:{int(target_duration + 0.999)}',
            '#EXT-X-MEDIA-SEQUENCE:0',
        ]
        if self.info_dict.get('is_live'):
            lines.append('#EXT-X-PLAYLIST-TYPE:EVENT')
        if self._init_name:
            lines.append(f'#EXT-X-MAP:URI="{self._init_name}"')
        for name, duration in self._segments:
            lines.extend((f'#EXTINF:{duration:.3f},', name))
        if endlist:
            lines.append('#EXT-X-ENDLIST')
        temporary = self.track_directory / 'playlist.m3u8.tmp'
        temporary.write_text('\n'.join(lines) + '\n', encoding='utf-8')
        os.replace(temporary, self.track_directory / 'playlist.m3u8')
        if self.stream_index is not None:
            self._write_master()

    def _write_master(self):
        playlists = {
            index: self.directory / name / 'playlist.m3u8'
            for index, name in ((0, 'video'), (1, 'audio'))
        }
        if not any(path.exists() for path in playlists.values()):
            return
        lines = ['#EXTM3U', '#EXT-X-VERSION:3']
        audio_playlist = playlists[1]
        video_playlist = playlists[0]
        if audio_playlist.exists():
            lines.append('#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="audio",NAME="default",DEFAULT=YES,AUTOSELECT=YES,URI="audio/playlist.m3u8"')
        if video_playlist.exists():
            info = self.info_dict
            video_info = self.master_state.get('video', info) if self.master_state else info
            audio_info = self.master_state.get('audio', {}) if self.master_state else {}
            bandwidth = int(sum((x.get('tbr') or 0) * 1000 for x in (video_info, audio_info)) or 1000000)
            video_codec = codec_string(video_info.get('vcodec'))
            audio_codec = codec_string(audio_info.get('acodec'))
            if not audio_codec and audio_playlist.exists():
                # Some HLS extractors leave acodec unset for an audio-only
                # MP4/M4A rendition even though the manifest identifies it.
                audio_codec = 'mp4a.40.2' if audio_info.get('ext') in ('mp4', 'm4a') else None
            codecs = ','.join(filter(None, (video_codec, audio_codec)))
            resolution = ''
            if video_info.get('width') and video_info.get('height'):
                resolution = f',RESOLUTION={video_info["width"]}x{video_info["height"]}'
            frame_rate = f',FRAME-RATE={video_info["fps"]}' if video_info.get('fps') else ''
            codec_attr = f',CODECS="{codecs}"' if codecs else ''
            lines.extend((
                f'#EXT-X-STREAM-INF:BANDWIDTH={bandwidth}{resolution}{frame_rate}{codec_attr}'
                + (',AUDIO="audio"' if audio_playlist.exists() else ''),
                'video/playlist.m3u8'))
        temporary = self.directory / 'master.m3u8.tmp'
        temporary.write_text('\n'.join(lines) + '\n', encoding='utf-8')
        os.replace(temporary, self.directory / 'master.m3u8')


def codec_string(codec):
    """Normalize yt-dlp codec names for HLS CODECS attributes."""
    if not codec or codec == 'none':
        return None
    if codec.startswith('avc1.') or codec.startswith('av01.') or codec.startswith('hev1.') or codec.startswith('hvc1.'):
        return codec
    if codec.startswith('avc'):
        return 'avc1'
    if codec.startswith('vp9'):
        return 'vp09'
    if codec.startswith('vp8'):
        return 'vp08'
    if codec.startswith('mp4a.') or codec.startswith('ac-3') or codec.startswith('ec-3'):
        return codec
    if codec.startswith('aac'):
        return 'mp4a.40.2'
    if codec.startswith('opus'):
        return 'opus'
    if codec.startswith('vorbis'):
        return 'vorbis'
    return codec
