import os
import subprocess
from pathlib import Path


class FFmpegDashOutput:
    """Transmux a progressive MPEG-TS input into a DASH directory."""

    def __init__(self, directory, logger=None):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.logger = logger
        self.process = subprocess.Popen([
            'ffmpeg', '-hide_banner', '-loglevel', 'error', '-f', 'mpegts', '-i', 'pipe:0',
            '-map', '0:v:0', '-map', '0:a:0', '-c', 'copy', '-tag:v', 'avc1', '-tag:a', 'mp4a',
            '-bsf:a', 'aac_adtstoasc',
            '-f', 'dash', '-seg_duration', '2',
            '-streaming', '1', '-window_size', '0', '-extra_window_size', '0',
            '-remove_at_exit', '0', '-init_seg_name',
            str(self.directory / 'init-$RepresentationID$.mp4'),
            '-media_seg_name',
            str(self.directory / 'chunk-$RepresentationID$-$Number$.m4s'),
            str(self.directory / 'manifest.mpd'),
        ], stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)

    def write_fragment(self, fragment, data):
        if fragment.get('is_init_segment'):
            return
        if self.process.stdin:
            try:
                self.process.stdin.write(data)
                self.process.stdin.flush()
            except BrokenPipeError as err:
                error = self.process.stderr.read().decode(errors='replace') if self.process.stderr else ''
                raise RuntimeError(f'FFmpeg DASH transmux stopped: {error.strip()}') from err

    def finalize(self):
        if self.process.stdin:
            self.process.stdin.close()
        self.process.wait()
        if self.process.returncode:
            error = self.process.stderr.read().decode(errors='replace') if self.process.stderr else ''
            if self.logger:
                self.logger.report_warning(f'FFmpeg DASH transmux failed: {error.strip()}')
