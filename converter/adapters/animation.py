"""Preserve delayed TPF2 keyframes while satisfying TPF3's zero-start rule.

TPF2 exports may omit the held state between the event trigger and its first
keyframe. A door indicator with times 2958.333 / 3000 must remain on until the
door is closed; subtracting 2958.333 would turn it off almost immediately.
Repeat its first state at zero instead. Existing keyframes keep their times.
"""
from __future__ import annotations

from copy import deepcopy
import math

from converter.adapters.tpf2_reader import diagnostic
from converter.resolver import array, table


def valid_times(times):
    return bool(times) and all(type(t) in (int, float) and math.isfinite(t) and t >= 0 for t in times)


def record_zero_start(diagnostics, source, location, before, after):
    diagnostics.append(diagnostic(
        'ANIMATION_ZERO_START_INSERTED',
        '開始時刻0に最初の状態を追加し、元の待ち時間と終了時刻を保持しました。',
        source, severity='info', rule_id='animation_zero_start_hold_v1',
        location=location, before=before, after=after,
        reason='最初の正時刻まで初期状態を保持し、閉ドア直前のランプ消灯などを早めないため。'))


def convert_animation(original, source, diagnostics):
    value = deepcopy(original)
    times, transforms = array(value.get('times', {})), array(value.get('transfs', {}))
    if times and transforms and len(times) != len(transforms):
        # Exporters sometimes leave one frame too many or too few; TPF2 uses the frames both lists have.
        count = min(len(times), len(transforms))
        diagnostics.append(diagnostic(
            'ANIMATION_LENGTH_TRIMMED',
            f'timesが{len(times)}個、transfsが{len(transforms)}個で数が合わないため、両方に存在する先頭{count}個だけを使います。',
            source, severity='warning'))
        times, transforms = times[:count], transforms[:count]
        value['times'], value['transfs'] = table(times), table(transforms)
    if valid_times(times) and len(times) == len(transforms) and times[0] > 0:
        after = [0, *times]
        value['times'] = table(after)
        value['transfs'] = table([deepcopy(transforms[0]), *transforms])
        record_zero_start(diagnostics, source, '/times', times, after)
    return value


def repair_keyframes(animation, source, location, diagnostics):
    """Make keyframe times strictly increasing and finite, as TPF3 requires. TPF2 evaluates whatever order the file has;
    the frames are put in time order (stable), frames with unusable times are dropped, and for equal times the later
    frame wins (it would override the earlier one when played)."""
    params = animation.get('params')
    if not isinstance(params, dict):
        return
    frames = [f for f in array(params.get('keyframes', {})) if isinstance(f, dict)]
    usable = [f for f in frames if type(f.get('time')) in (int, float) and math.isfinite(f['time']) and f['time'] >= 0]
    ordered = sorted(usable, key=lambda f: f['time'])
    unique = []
    for frame in ordered:
        if unique and unique[-1]['time'] == frame['time']:
            unique[-1] = frame
        else:
            unique.append(frame)
    original = array(params.get('keyframes', {}))
    if unique != original:
        params['keyframes'] = table(unique)
        diagnostics.append(diagnostic('ANIMATION_KEYFRAMES_REPAIRED',
            f'キーフレームの時刻が昇順でない・重複・不正だったため、時刻順に並べ直しました（{len(original)}個→{len(unique)}個）。',
            source, severity='warning', location=location))


def normalize_embedded_animation(animation, source, location, diagnostics):
    """In-place normalization on the writer's deep copy of a node animation."""
    if animation.get('type') not in {'KEYFRAME', 'KEYFRAME_MATRIX'}:
        return
    repair_keyframes(animation, source, location, diagnostics)
    params = animation.get('params', {})
    frames = array(params.get('keyframes', {}))
    times = [frame.get('time') if isinstance(frame, dict) else None for frame in frames]
    if valid_times(times) and times[0] > 0:
        initial = deepcopy(frames[0])
        initial['time'] = 0
        params['keyframes'] = table([initial, *frames])
        record_zero_start(diagnostics, source, location + '/params/keyframes', times, [0, *times])


def drop_empty_animation(animations, name, source, diagnostics):
    """Remove a keyframe animation that has no keyframes at all (TPF2 plays nothing; TPF3 rejects it). True if removed."""
    animation = animations.get(name)
    if isinstance(animation, dict) and animation.get('type') in {'KEYFRAME', 'KEYFRAME_MATRIX'}             and not array(animation.get('params', {}).get('keyframes', {}) if isinstance(animation.get('params'), dict) else {}):
        del animations[name]
        diagnostics.append(diagnostic('ANIMATION_EMPTY_DROPPED', f'キーフレームが1つもないアニメーション {name} を省きました。', source, severity='warning'))
        return True
    return False


def repair_mesh_animations(value, source, diagnostics):
    """Animations that live inside a .msh file get the same repairs as the ones in models."""
    stack = [('', value)]
    while stack:
        location, item = stack.pop()
        if isinstance(item, dict):
            if item.get('type') in {'KEYFRAME', 'KEYFRAME_MATRIX'}:
                normalize_embedded_animation(item, source, location, diagnostics)
            if location.endswith('/animations') or location == '/animations':
                for name in list(item):
                    drop_empty_animation(item, name, source, diagnostics)
            stack.extend((f'{location}/{key}', child) for key, child in item.items() if isinstance(child, dict))


def playback_duration(times):
    """The event starts at zero, including any initial held interval."""
    return max(times) if valid_times(times) else None
