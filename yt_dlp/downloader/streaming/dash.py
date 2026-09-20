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
            container = track_container(info, data)
            if not container:
                if self.logger:
                    self.logger.report_warning(
                        'Skipping streaming DASH output for an unsupported fragment container')
                return
            track = self._tracks.setdefault(stream_index, {
                'info': info,
                'container': container,
                'segments': [],
                'init': False,
            })
            # Some DASH generators return the initialization segment together
            # with the first media segment instead of exposing it separately.
            # Split the ISO-BMFF boxes so the MPD can reference a real init
            # segment and media chunks can start at the first `moof` box.
            if not track['init'] and container == 'mp4' and not fragment.get('is_init_segment'):
                moof = data.find(b'moof')
                if moof > 4:
                    moof -= 4
                    path = self.directory / f'init-{stream_index}.{container}'
                    path.write_bytes(data[:moof])
                    track['init'] = True
                    data = data[moof:]
            elif not track['init'] and container == 'webm' and not fragment.get('is_init_segment'):
                # YouTube's WebM DASH fragments may repeat the EBML/Tracks
                # header in the first fragment. Keep that header as the init
                # segment and publish the Cluster as the media segment.
                cluster = data.find(b'\x1f\x43\xb6\x75')
                if cluster > 0:
                    path = self.directory / f'init-{stream_index}.webm'
                    path.write_bytes(data[:cluster])
                    track['init'] = True
                    data = data[cluster:]
            elif track['init'] and container == 'webm' and not fragment.get('is_init_segment'):
                # Later WebM fragments can repeat their EBML header. Strip it
                # before writing the Cluster referenced by the MPD.
                cluster = data.find(b'\x1f\x43\xb6\x75')
                if cluster > 0:
                    data = data[cluster:]
            if fragment.get('is_init_segment'):
                if not track['init']:
                    path = self.directory / f'init-{stream_index}.{container}'
                    path.write_bytes(data)
                    track['init'] = True
                return

            if not fragment.get('duration'):
                return
            number = len(track['segments']) + 1
            extension = 'm4s' if container == 'mp4' else 'webm'
            path = self.directory / f'chunk-{stream_index}-{number}.{extension}'
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
            container = track['container']
            is_audio = info.get('vcodec') == 'none'
            content_type = 'audio' if is_audio else 'video'
            codec = info.get('acodec') if is_audio else info.get('vcodec')
            codec = codec or 'unknown'
            mime = f'{content_type}/{container}'
            attrs = [f'id="{index}"', f'contentType="{content_type}"', f'mimeType="{mime}"']
            representation = [f'id="{index}"', f'bandwidth="{int((info.get("tbr") or 1000) * 1000)}"', f'codecs="{escape(codec)}"']
            if not is_audio and info.get('width') and info.get('height'):
                representation += [f'width="{info["width"]}"', f'height="{info["height"]}"']
            timeline = ''.join(f'<S t="{round(segment["start"] * 1000)}" d="{round(segment["duration"] * 1000)}" />' for segment in track['segments'])
            init_name = f'init-{index}.{container}'
            media_extension = 'm4s' if container == 'mp4' else 'webm'
            tracks.append(f'''  <AdaptationSet {' '.join(attrs)}>
    <Representation {' '.join(representation)}>
        <SegmentTemplate timescale="1000" initialization="{init_name}" media="chunk-{index}-$Number$.{media_extension}" startNumber="1">
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


def track_container(info, data):
    """Return the DASH container represented by a fragment."""
    if b'ftyp' in data[:64] or info.get('protocol', '').startswith('http_dash') and info.get('ext') != 'webm':
        return 'mp4'
    if data.startswith(b'\x1a\x45\xdf\xa3') or info.get('ext') == 'webm' or info.get('vcodec', '').startswith('vp9'):
        return 'webm'
    return None