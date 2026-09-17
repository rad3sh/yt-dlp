import os
import threading
from pathlib import Path
from xml.sax.saxutils import escape


class DashOutput:
    """Write native DASH fragments and a conservative, progressive MPD."""

    def __init__(self, directory, logger=None):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.logger = logger
        self._tracks = {}
        self._lock = threading.Lock()

    def write_fragment(self, fragment, data):
        stream_index = fragment.get('_streaming_stream_index')
        if stream_index is None:
            stream_index = fragment.get('stream_index', 0)
        info = fragment.get('_streaming_info') or {}
        with self._lock:
            if not fragment.get('is_init_segment') and not trackable_mp4(info, data):
                if self.logger:
                    self.logger.report_warning(
                        'Skipping streaming DASH output for a non-ISO-BMFF fragment')
                return
            track = self._tracks.setdefault(stream_index, {
                'info': info,
                'segments': [],
                'init': False,
            })
            if fragment.get('is_init_segment'):
                if not track['init']:
                    path = self.directory / f'init-{stream_index}.mp4'
                    path.write_bytes(data)
                    track['init'] = True
                return

            if not fragment.get('duration'):
                return
            number = len(track['segments']) + 1
            path = self.directory / f'chunk-{stream_index}-{number}.m4s'
            path.write_bytes(data)
            start = sum(segment['duration'] for segment in track['segments'])
            track['segments'].append({'number': number, 'start': start, 'duration': fragment['duration']})
            self._write_manifest()

    def finalize(self):
        with self._lock:
            self._write_manifest(static=True)

    def _write_manifest(self, static=False):
        tracks = []
        for index, track in sorted(self._tracks.items()):
            if not track['segments']:
                continue
            info = track['info']
            is_audio = info.get('vcodec') == 'none'
            content_type = 'audio' if is_audio else 'video'
            codec = info.get('acodec') if is_audio else info.get('vcodec')
            codec = codec or 'unknown'
            attrs = [f'id="{index}"', f'contentType="{content_type}"', 'mimeType="audio/mp4"' if is_audio else 'mimeType="video/mp4"']
            representation = [f'id="{index}"', f'bandwidth="{int((info.get("tbr") or 1000) * 1000)}"', f'codecs="{escape(codec)}"']
            if not is_audio and info.get('width') and info.get('height'):
                representation += [f'width="{info["width"]}"', f'height="{info["height"]}"']
            timeline = ''.join(f'<S t="{round(segment["start"] * 1000)}" d="{round(segment["duration"] * 1000)}" />' for segment in track['segments'])
            tracks.append(f'''  <AdaptationSet {' '.join(attrs)}>
    <Representation {' '.join(representation)}>
      <SegmentTemplate timescale="1000" initialization="init-{index}.mp4" media="chunk-{index}-$Number$.m4s" startNumber="1">
        <SegmentTimeline>{timeline}</SegmentTimeline>
      </SegmentTemplate>
    </Representation>
  </AdaptationSet>''')
        if not tracks:
            return
        total_duration = max(sum(segment['duration'] for segment in track['segments']) for track in self._tracks.values())
        mpd_type = 'static' if static else 'dynamic'
        duration = f' mediaPresentationDuration="PT{total_duration:.3f}S"' if static else ''
        text = f'''<?xml version="1.0" encoding="UTF-8"?>
<MPD xmlns="urn:mpeg:dash:schema:mpd:2011" type="{mpd_type}" minBufferTime="PT1S" profiles="urn:mpeg:dash:profile:isoff-live:2011"{duration}>
  <Period start="PT0S">
{os.linesep.join(tracks)}
  </Period>
</MPD>
'''
        temporary = self.directory / 'manifest.mpd.tmp'
        temporary.write_text(text, encoding='utf-8')
        os.replace(temporary, self.directory / 'manifest.mpd')


def trackable_mp4(info, data):
    """Return whether a fragment can be represented by the DASH MPD writer.

    The initial implementation only supports fragmented ISO-BMFF. HLS MPEG-TS
    fragments must use the future HLS output backend or an FFmpeg remuxer.
    """
    return b'ftyp' in data[:64] or info.get('protocol', '').startswith('http_dash')