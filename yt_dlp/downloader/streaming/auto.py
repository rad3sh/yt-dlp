from .dash import DashOutput
from .ffmpeg_dash import FFmpegDashOutput
from .hls import HlsOutput


def is_mpegts(data):
    return bool(data) and (data[:1] == b'\x47' or len(data) > 188 and data[188:189] == b'\x47')


class AutoOutput:
    """Select the lightest output backend after inspecting the first fragment."""

    def __init__(self, directory, output_format, logger=None):
        self.directory = directory
        self.output_format = output_format
        self.logger = logger
        self.backend = None

    def write_fragment(self, fragment, data):
        if self.backend is None:
            if fragment.get('is_init_segment') or is_mpegts(data):
                self.backend = HlsOutput(self.directory, self.logger) if self.output_format == 'auto' else FFmpegDashOutput(self.directory, self.logger)
            else:
                self.backend = DashOutput(self.directory, self.logger)
            if self.logger:
                self.logger.to_screen(
                    f'[streaming] Selected {type(self.backend).__name__} backend')
        self.backend.write_fragment(fragment, data)

    def finalize(self):
        if self.backend:
            self.backend.finalize()
