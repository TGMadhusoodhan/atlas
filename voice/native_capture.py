"""Experimental native-array capture feeding the existing RealtimeSTT conversation.

No files, no default-route changes, no automatic filter/beamforming promotion.
"""
import json
import subprocess
import threading
import time
from collections import deque

import numpy as np
from scipy.signal import firwin, lfilter
from capture_source import resolve_source


class StreamingDecimator:
    """Stateful anti-alias FIR for 48 -> 16 kHz, including chunk phase continuity."""
    def __init__(self):
        self.taps = firwin(127, 7200, fs=48000)
        self.state = np.zeros(len(self.taps)-1)
        self.offset = 0

    def process(self, mono):
        filtered, self.state = lfilter(self.taps, [1.0], mono, zi=self.state)
        first = (-self.offset) % 3
        self.offset = (self.offset + len(mono)) % 3
        return filtered[first::3].astype(np.float32)


class NativeCapture:
    def __init__(self, recorder, source_name, *, channel='average', processed=False, native=True):
        sources = json.loads(subprocess.check_output(['pactl','-f','json','list','sources'], timeout=5))
        source = resolve_source(source_name, sources, require_internal=not processed)
        spec = source['sample_specification'].split()
        self.channels, self.rate = int(spec[1][:-2]), int(spec[2][:-2])
        if native and self.rate != 48000:
            raise ValueError('Experimental native frontend currently requires a verified 48 kHz source')
        if channel != 'average' and not 0 <= int(channel) < self.channels:
            raise ValueError('Channel is outside the physical source channel count')
        if not native:
            self.rate, self.channels, channel = 16000, 1, 'average'
        self.recorder, self.channel = recorder, channel
        self.decimator = StreamingDecimator() if native else None
        self.ready = threading.Event()
        self.level_history = deque(maxlen=6000)
        self.level_lock = threading.Lock()
        self.failure = None
        self.stop_event = threading.Event()
        self.process = subprocess.Popen(['pw-record', '--target', source_name, '--rate',str(self.rate), '--quality','10',
            '--channels', str(self.channels), '--format','f32', '--raw', '--volume','1',
            '--properties', '{ application.name = "AtlasCapture" application.id = "org.atlas.voice" node.dont-reconnect = true }', '-'],
            stdout=subprocess.PIPE, stderr=None)
        self.thread = threading.Thread(target=self._pump, daemon=True)
        self.thread.start()

    def _pump(self):
        try:
            frame_bytes = self.rate // 100 * self.channels * 4
            pending = bytearray()
            while not self.stop_event.is_set():
                data = self.process.stdout.read(frame_bytes - len(pending))
                if not data:
                    raise RuntimeError('Pinned capture source stopped; refusing route fallback')
                pending.extend(data)
                if len(pending) != frame_bytes:
                    continue
                x = np.frombuffer(bytes(pending), dtype='<f4').reshape(-1,self.channels)
                with self.level_lock:
                    self.level_history.append((time.monotonic(), float(np.mean(np.abs(x) >= .999))))
                mono = x.mean(axis=1) if self.channel == 'average' else x[:,int(self.channel)]
                if not np.isfinite(mono).all():
                    raise RuntimeError('Nonfinite capture samples')
                audio = self.decimator.process(mono) if self.decimator else mono
                # No positive gain. Saturation is guarded; diagnostics still observe full-scale samples.
                pcm = (np.clip(audio, -1, 32767/32768) * 32768).astype('<i2').tobytes()
                self.recorder.feed_audio(pcm)
                self.ready.set()
                pending.clear()
        except Exception as error:
            if not self.stop_event.is_set():
                self.failure = str(error)
                self.ready.set()
                self.recorder.abort()

    def source_clipping(self, start, end):
        if start is None or end is None:
            return None
        with self.level_lock:
            values = [value for timestamp,value in self.level_history if start-.3 <= timestamp <= end]
        return max(values) if values else None

    def close(self):
        self.stop_event.set()
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=3)
        self.thread.join(3)
        self.process.stdout.close()
