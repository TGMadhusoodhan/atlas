"""Local numeric pipeline telemetry. Never contains transcripts or PCM."""
import json
import time
from pathlib import Path


class PipelineTelemetry:
    def __init__(self, path=None):
        self.path = Path(path).expanduser() if path else None

    def record(self, evidence, outcome, *, handoff=False, utterance_id=None):
        if self.path is None:
            return
        timings = dict(getattr(evidence, 'timings', None) or {})
        now = time.monotonic()
        timings['request_handoff' if handoff else 'transcript_decision'] = now
        end = timings.get('speech_end')
        payload = {'schema_version': 2, 'timestamp': time.time(), 'utterance_id': utterance_id,
            'event': 'handoff' if handoff else 'decision', 'decision': outcome,
            'timings_monotonic_s': timings,
            'speech_end_to_event_ms': (now-end)*1000 if end is not None else None,
            'fallback_used': getattr(evidence,'fallback_used',False),
            'fallback_error': getattr(evidence,'fallback_error',None),
            'estimated_snr_db': getattr(evidence,'snr_db',None),
            'clipping_fraction': getattr(evidence,'clipping_fraction',None),
            'vad_probability': getattr(evidence,'vad_probability',None)}
        self.path.parent.mkdir(parents=True,exist_ok=True)
        with self.path.open('a') as stream:
            stream.write(json.dumps(payload,allow_nan=False) + '\n')
        self.path.chmod(0o600)


def summarize(rows):
    import numpy as np
    intervals = {
        'speech_duration_ms': ('speech_onset','speech_end'),
        'end_to_preprocessing_ms': ('speech_end','preprocessing_complete'),
        'end_to_first_asr_ms': ('speech_end','first_asr_complete'),
        'end_to_fallback_ms': ('speech_end','fallback_asr_complete'),
        'end_to_decision_ms': ('speech_end','transcript_decision'),
        'end_to_handoff_ms': ('speech_end','request_handoff'),
    }
    result = {}
    for name,(start,end) in intervals.items():
        # Handoff repeats earlier timestamps; use decisions for earlier stages to avoid double counting.
        event = 'handoff' if name.endswith('handoff_ms') else 'decision'
        values = []
        for row in rows:
            t = row.get('timings_monotonic_s',{})
            if row.get('event') == event and t.get(start) is not None and t.get(end) is not None:
                values.append((t[end]-t[start])*1000)
        result[name] = {'count':len(values),'p50':float(np.percentile(values,50)),
                       'p95':float(np.percentile(values,95))} if values else None
    decisions = [r for r in rows if r.get('event')=='decision']
    result['decisions'] = {outcome:sum(r.get('decision')==outcome for r in decisions)
                           for outcome in ('ACCEPT','CLARIFY','REJECT')}
    result['wrong_accept_rate'] = None  # no ground-truth labels in numeric runtime telemetry
    return result


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('jsonl',type=Path)
    args = parser.parse_args()
    rows = [json.loads(line) for line in args.jsonl.read_text().splitlines() if line.strip()]
    print(json.dumps(summarize(rows),indent=2))
