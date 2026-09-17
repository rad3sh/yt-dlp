import os
import threading
from pathlib import Path


class HlsOutput:
    """Write downloaded HLS media fragments and a progressively updated playlist."""

    def __init__(self, directory, logger=None):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.logger = logger
        self._segments = []
        self._init_name = None
        self._lock = threading.Lock()

    def write_fragment(self, fragment, data):
        with self._lock:
            if fragment.get('is_init_segment'):
                self._init_name = 'init.mp4'
                (self.directory / self._init_name).write_bytes(data)
                self._write_playlist()
                return
            duration = fragment.get('duration') or 2.0
            number = len(self._segments) + 1
            name = f'segment-{number:05d}.ts'
            (self.directory / name).write_bytes(data)
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
        if self._init_name:
            lines.append(f'#EXT-X-MAP:URI="{self._init_name}"')
        for name, duration in self._segments:
            lines.extend((f'#EXTINF:{duration:.3f},', name))
        if endlist:
            lines.append('#EXT-X-ENDLIST')
        temporary = self.directory / 'playlist.m3u8.tmp'
        temporary.write_text('\n'.join(lines) + '\n', encoding='utf-8')
        os.replace(temporary, self.directory / 'playlist.m3u8')
