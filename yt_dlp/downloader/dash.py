import os
import time
import urllib.parse

from .streaming.dash import DashOutput
from .streaming.hls import HlsOutput
from .streaming.auto import AutoOutput
from . import get_suitable_downloader
from .fragment import FragmentFD
from ..utils import ReExtractInfo, update_url_query, urljoin


class DashSegmentsFD(FragmentFD):
    """
    Download segments in a DASH manifest. External downloaders can take over
    the fragment downloads by supporting the 'dash_frag_urls' protocol
    """

    FD_NAME = 'dashsegments'

    def real_download(self, filename, info_dict):
        streaming_output = None
        streaming_output_format = self.params.get('streaming_output_format')
        if streaming_output_format:
            if not self.params.get('streaming_output_path'):
                self.report_error('--streaming-output-path is required with --streaming-output-format')
                return False
            if self.params.get('concurrent_fragment_downloads', 1) != 1:
                self.report_error('--streaming-output-format currently requires --concurrent-fragments 1')
                return False
            output_path = self.ydl.evaluate_outtmpl(self.params['streaming_output_path'], info_dict)
            if streaming_output_format == 'hls':
                streaming_output = HlsOutput(output_path, logger=self.ydl)
            elif streaming_output_format == 'dash':
                streaming_output = AutoOutput(output_path, 'dash', logger=self.ydl)
            else:
                streaming_output = AutoOutput(output_path, 'auto', logger=self.ydl)
            streaming_temp_dir = os.path.join(output_path, '.fragments')

        if 'http_dash_segments_generator' in info_dict['protocol'].split('+'):
            real_downloader = None  # No external FD can support --live-from-start
        else:
            if info_dict.get('is_live'):
                self.report_error('Live DASH videos are not supported')
            real_downloader = get_suitable_downloader(
                info_dict, self.params, None, protocol='dash_frag_urls', to_stdout=(filename == '-'))

        real_start = time.time()

        requested_formats = [{**info_dict, **fmt} for fmt in info_dict.get('requested_formats', [])]
        args = []
        for fmt in requested_formats or [info_dict]:
            # Re-extract if --load-info-json is used and 'fragments' was originally a generator
            # See https://github.com/yt-dlp/yt-dlp/issues/13906
            if isinstance(fmt['fragments'], str):
                raise ReExtractInfo('the stream needs to be re-extracted', expected=True)

            try:
                fragment_count = 1 if self.params.get('test') else len(fmt['fragments'])
            except TypeError:
                fragment_count = None
            ctx = {
                'filename': fmt.get('filepath') or filename,
                'live': 'is_from_start' if fmt.get('is_from_start') else fmt.get('is_live'),
                'total_frags': fragment_count,
            }
            ctx['streaming_output'] = streaming_output
            ctx['streaming_stream_index'] = len(args)
            ctx['streaming_only'] = bool(streaming_output)
            ctx['streaming_temp_dir'] = streaming_temp_dir

            if real_downloader:
                self._prepare_external_frag_download(ctx)
            else:
                self._prepare_and_start_frag_download(ctx, fmt)
            ctx['start'] = real_start

            extra_query = None
            extra_param_to_segment_url = info_dict.get('extra_param_to_segment_url')
            if extra_param_to_segment_url:
                extra_query = urllib.parse.parse_qs(extra_param_to_segment_url)

            fragments_to_download = self._get_fragments(fmt, ctx, extra_query)

            if real_downloader:
                self.to_screen(
                    f'[{self.FD_NAME}] Fragment downloads will be delegated to {real_downloader.get_basename()}')
                info_dict['fragments'] = list(fragments_to_download)
                fd = real_downloader(self.ydl, self.params)
                return fd.real_download(filename, info_dict)

            args.append([ctx, fragments_to_download, fmt])

        try:
            return self.download_and_append_fragments_multiple(
                *args, is_fatal=lambda idx: idx == 0,
                fragment_callback=(streaming_output.write_fragment if streaming_output else None))
        finally:
            if streaming_output:
                streaming_output.finalize()

    def _resolve_fragments(self, fragments, ctx):
        fragments = fragments(ctx) if callable(fragments) else fragments
        return [next(iter(fragments))] if self.params.get('test') else fragments

    def _get_fragments(self, fmt, ctx, extra_query):
        fragment_base_url = fmt.get('fragment_base_url')
        fragments = self._resolve_fragments(fmt['fragments'], ctx)

        frag_index = 0
        for i, fragment in enumerate(fragments):
            frag_index += 1
            if frag_index <= ctx['fragment_index']:
                continue
            fragment_url = fragment.get('url')
            if not fragment_url:
                assert fragment_base_url
                fragment_url = urljoin(fragment_base_url, fragment['path'])
            if extra_query:
                fragment_url = update_url_query(fragment_url, extra_query)

            yield {
                'frag_index': frag_index,
                'fragment_count': fragment.get('fragment_count'),
                'index': i,
                'url': fragment_url,
                '_streaming_stream_index': ctx.get('streaming_stream_index'),
                '_streaming_info': fmt,
                'duration': fragment.get('duration'),
                'is_init_segment': fragment.get('is_init_segment', False),
            }
