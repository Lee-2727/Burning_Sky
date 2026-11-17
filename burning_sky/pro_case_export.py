"""Build a faithful Pro archive from current results + confirmed satellite edits.

The original forecast and accepted correction are both retained. Never export a
stale satellite correction attached to a different baseline or modified model
batch, and never make network requests while assembling a ZIP.
"""
from __future__ import annotations
import copy
from . import advanced_bridge


def prepare_case(results, originals, meta, request_cache, to_safe, *, reader=None, cache_dir=None):
    reader = reader or advanced_bridge.read
    cache_dir = cache_dir or advanced_bridge.CACHE_DIR
    safe_current = to_safe(results or {})
    safe_original = to_safe(originals or results or {})
    saved = copy.deepcopy(safe_current)
    requests = copy.deepcopy(to_safe(request_cache or {}))
    corrections = {}
    images = {}
    for direction in ('morning','evening'):
        if direction not in safe_current:
            continue
        config = advanced_bridge.config_from_main(meta,direction)
        if config is None:
            continue
        # A manual cloud replay is a different baseline; never silently replace it
        # with a satellite correction made for an earlier unedited forecast.
        if safe_current[direction] != safe_original.get(direction):
            continue
        ident=advanced_bridge.identity(config)
        base=reader(ident+'_base')
        corrected=reader(ident+'_corrected')
        if (not isinstance(base,dict) or not isinstance(corrected,dict)
                or not advanced_bridge.same_config(base.get('config',{}),config)
                or not advanced_bridge.same_config(corrected.get('config',{}),config)
                or base.get('result')!=safe_original.get(direction)
                or not isinstance(corrected.get('result'),dict)
                or not isinstance(corrected['result'].get('inferred'),dict)):
            continue
        saved[direction]=copy.deepcopy(corrected['result'])
        requests.update(to_safe(corrected.get('request_cache') or {}))
        corrections[direction]={
            'original_result':safe_original[direction],
            'edits':corrected.get('edits') or [],
            'observation_time':corrected.get('observation_time'),
            'registration_quality':corrected.get('registration_quality'),
            'saved_at':corrected.get('saved_at'),
        }
        img=cache_dir/(ident+'_corrected.jpg')
        if img.is_file():
            images[direction]=img.read_bytes()
    return saved,requests,corrections,images
