#!/usr/bin/env python3
"""Temporary PipeWire WebRTC AEC experiment; never changes default devices.

Playback must be explicitly routed through AtlasLabPlayback for a valid reference.
The virtual source and sink are removed on exit, including exceptions/Ctrl-C.
"""
import argparse
import json
import subprocess
from contextlib import contextmanager

from capture_source import resolve_source


@contextmanager
def aec_source(source, sink, denoise=False):
    sources = json.loads(subprocess.check_output(['pactl', '-f', 'json', 'list', 'sources']))
    resolve_source(source, sources)
    module = subprocess.check_output([
        'pactl', 'load-module', 'module-echo-cancel', f'source_master={source}', f'sink_master={sink}',
        'source_name=AtlasLabMicrophone', 'sink_name=AtlasLabPlayback', 'rate=48000', 'channels=2',
        'aec_method=webrtc', 'source_properties=device.description=Atlas_Lab_Microphone',
        'aec_args="webrtc.gain_control=false webrtc.noise_suppression=' +
        str(denoise).lower() + ' webrtc.high_pass_filter=false"',
    ], text=True, timeout=10).strip()
    try:
        yield {'module_id': module, 'source': 'AtlasLabMicrophone', 'sink': 'AtlasLabPlayback',
               'aec': True, 'webrtc_denoise': denoise, 'automatic_gain': False}
    finally:
        subprocess.run(['pactl', 'unload-module', module], check=True, timeout=10)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source', required=True)
    p.add_argument('--sink', required=True)
    p.add_argument('--denoise', action='store_true')
    p.add_argument('--smoke', action='store_true', help='Verify nodes then immediately unload; no recordings')
    args = p.parse_args()
    with aec_source(args.source, args.sink, args.denoise) as info:
        sources = json.loads(subprocess.check_output(['pactl', '-f', 'json', 'list', 'sources']))
        if not any(s['name'] == info['source'] for s in sources):
            raise RuntimeError('AEC source was not created')
        print(json.dumps(info), flush=True)
        if not args.smoke:
            input('Route test playback through AtlasLabPlayback; record only with explicit consent. Enter to remove: ')
    print('Temporary AEC nodes removed.')


if __name__ == '__main__':
    main()
