"""Resolve a stable PipeWire/Pulse source without changing desktop defaults."""
import json
import os
import subprocess


def resolve_source(name, sources, *, require_internal=True):
    matches = [s for s in sources if s.get("name") == name]
    if len(matches) != 1 or str(name).endswith(".monitor"):
        raise RuntimeError(f"Configured capture source unavailable: {name}")
    source = matches[0]
    if require_internal and source.get("active_port") != "analog-input-internal-mic":
        raise RuntimeError("Configured source is not on its built-in microphone port")
    if source.get("mute"):
        raise RuntimeError("Configured microphone is muted")
    return source


def pin_pulse_source(name, *, require_internal=True):
    result = subprocess.run(["pactl", "-f", "json", "list", "sources"],
                            capture_output=True, text=True, check=True, timeout=5)
    source = resolve_source(name, json.loads(result.stdout), require_internal=require_internal)
    os.environ["PULSE_SOURCE"] = name
    os.environ["PULSE_PROP"] = "application.name=AtlasVoice application.id=org.atlas.voice"
    return source


def verify_pulse_capture(name, *, require_internal=True):
    """Fail closed if session-manager rerouting or restored attenuation occurs."""
    source_result = subprocess.run(['pactl','-f','json','list','sources'], capture_output=True,
                                   text=True, check=True, timeout=5)
    sources = json.loads(source_result.stdout)
    selected = resolve_source(name, sources, require_internal=require_internal)
    streams_result = subprocess.run(['pactl','-f','json','list','source-outputs'], capture_output=True,
                                    text=True, check=True, timeout=5)
    streams = [s for s in json.loads(streams_result.stdout)
               if s.get('properties',{}).get('application.id') == 'org.atlas.voice']
    if not streams:
        raise RuntimeError('Atlas pinned capture stream is unavailable')
    for stream in streams:
        if stream['source'] != selected['index'] or stream.get('mute'):
            raise RuntimeError('Atlas capture was rerouted or muted; transcript discarded')
        volumes = stream.get('volume',{}).values()
        if not volumes or any(abs(v['value'] - 65536) > 655 for v in volumes):
            raise RuntimeError('Atlas recording gain is not unity; check application recording volume')


def wait_for_pulse_capture(name, *, require_internal=True, timeout=8):
    """RealtimeSTT starts its audio worker asynchronously; don't announce readiness early."""
    import time
    deadline = time.monotonic() + timeout
    while True:
        try:
            verify_pulse_capture(name, require_internal=require_internal)
            return
        except RuntimeError as error:
            if time.monotonic() >= deadline:
                raise
            time.sleep(.1)
