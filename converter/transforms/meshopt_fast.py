"""Faster meshoptimizer fallback: same trials, same result as ``meshopt._simplify_meshopt_reference``.

The reference runs up to 36 trials (error x fraction) and calls ``lod._pack`` for each one only to learn
``len(blob)``. Here a native (WASM, tools/strict_lod/src/trial.rs) evaluator computes that exact size from the
simplifier output, the simplifier inputs are uploaded once per component, and the real ``_pack`` runs once, for
the chosen trial. The chosen trial is rebuilt with the reference code path and its packed size must equal the
native prediction; any mismatch (or any native problem) makes the caller use the reference instead.
"""
from array import array
import math
import struct

from .lod import _decode, _pack, _normal, _cancel, _array
from .meshopt import Runtime, components, _prepare
from .strict_lod import Runtime as NativeRuntime, NativeUnavailable

TRIAL_MAGIC = 0x54505331
_CHANNELS = ('normal', 'tangent', 'uv0', 'uv1')
_BITS = {'normal': 2, 'tangent': 4, 'uv0': 8, 'uv1': 16}
_ERRORS = (.01, .025, .05, .1, .15, .2)
_FRACTIONS = (.65, .45, .3, .2, .12, .07)


class NativeMismatch(Exception):
    pass


class _Group:
    """Simplifier inputs of one component, uploaded once into the meshoptimizer instance."""

    def __init__(self, runtime, indices, vectors, attrs, weights, locks):
        ex, store = runtime.exports, runtime.store
        memory, sbrk = ex['memory'], ex['sbrk']

        def upload(data):
            at = sbrk(store, len(data))
            memory.write(store, data, at)
            return at
        self.count = len(indices)
        self.vertex_count = len(locks)
        self.stride = len(weights)
        self.ti = upload(b'\0' * (self.count * 4))
        self.sp = upload(struct.pack('<' + 'f' * len(vectors), *vectors))
        self.sa = upload(struct.pack('<' + 'f' * len(attrs), *attrs))
        self.sw = upload(struct.pack('<' + 'f' * len(weights), *weights))
        self.si = upload(struct.pack('<' + 'I' * self.count, *indices))
        self.vl = upload(bytes(locks))


def _simplify_group(runtime, te, group, target, error):
    ex, store = runtime.exports, runtime.store
    result = ex['meshopt_simplifyWithAttributes'](store, group.ti, group.si, group.count, group.sp,
                                                  group.vertex_count, 12, group.sa, group.stride * 4, group.sw,
                                                  group.stride, group.vl, target, error, 4 | 32, te)
    memory = ex['memory']
    raw = memory.read(store, group.ti, group.ti + result * 4)
    measured = struct.unpack('<f', memory.read(store, te, te + 4))[0]
    selected = array('I')
    selected.frombytes(raw)
    if result % 3 or (len(selected) and max(selected) >= group.vertex_count) or not math.isfinite(measured):
        raise ValueError('Invalid meshoptimizer output')
    return raw, measured


def _build_evaluator(native, definition, positions, prepared):
    """Upload the corner table / groups to the native trial evaluator; returns (selected_ptr, lens_ptr, lens)."""
    comps = {}
    for name in _CHANNELS:
        spec = definition['vertexAttr'].get(name)
        comps[name] = int(spec['numComp']) if spec else 0
    subs = _array(definition['subMeshes'])
    sub_mask = array('I', [1 | sum(bit for name, bit in _BITS.items() if name in sub['indices']) for sub in subs])
    stride = sum(comps.values())
    pos = array('d')
    for p in positions:
        pos.extend(p)
    vid = array('I')
    attr = array('d')
    group_info = array('I')
    orig_idx = array('I')
    zeros = {name: (0.0,) * comps[name] for name in _CHANNELS}
    corner_start = 0
    for group, corners, indices, *_ in prepared:
        sub = group[0]['sub']
        group_info.extend((corner_start, len(corners), sub, len(orig_idx), len(indices)))
        orig_idx.extend(indices)
        mask = sub_mask[sub]
        for v, values in corners:
            vid.append(v)
            for name in _CHANNELS:
                value = values.get(name) if mask & _BITS[name] else None
                if value is None:
                    attr.extend(zeros[name])
                else:
                    if len(value) != comps[name]:
                        raise NativeUnavailable('attribute component count mismatch')
                    attr.extend(value)
        corner_start += len(corners)
    ngroup = len(prepared)
    ptr_pos = native.alloc_bytes(pos.tobytes())
    ptr_vid = native.alloc_bytes(vid.tobytes())
    ptr_attr = native.alloc_bytes(attr.tobytes())
    ptr_groups = native.alloc_bytes(group_info.tobytes())
    ptr_idx = native.alloc_bytes(orig_idx.tobytes())
    ptr_mask = native.alloc_bytes(sub_mask.tobytes())
    header = array('I', [TRIAL_MAGIC, len(positions), len(vid), ngroup, len(subs),
                         comps['normal'], comps['tangent'], comps['uv0'], comps['uv1'],
                         ptr_pos, ptr_vid, ptr_attr, ptr_groups, ptr_idx, len(orig_idx), ptr_mask,
                         3, 5])  # shading digits (shading_step 1e-3), uv digits (quantize_uv)
    code = native.call('tp_init', native.alloc_bytes(header.tobytes()))
    if code != 0:
        raise NativeUnavailable(f'tp_init failed: {code}')
    return native.alloc_bytes(b'\0' * (4 * max(1, len(orig_idx)))), native.alloc_bytes(b'\0' * (4 * max(1, ngroup)))


def _faces_of(positions, prepared, selections):
    """The reference per-trial face conversion (meshopt._simplify_meshopt_reference), verbatim in behaviour."""
    output = []
    max_error = 0.
    unchanged = 0
    degenerate = 0
    for (group, corners, indices, *_), (selected, measured) in zip(prepared, selections):
        converted = []
        for i in range(0, len(selected), 3):
            chosen = [corners[j] for j in selected[i:i + 3]]
            ids = [v for v, a in chosen]
            normal = _normal([positions[v] for v in ids])
            if normal is None or len(set(ids)) != 3:
                degenerate += 1
                continue
            converted.append({'ids': ids, 'attrs': [dict(a) for v, a in chosen],
                              'sub': group[0]['sub'], 'normal': normal})
        if not converted:
            converted = group
            unchanged += 1
        output.extend(converted)
        max_error = max(max_error, measured)
    return output, max_error, unchanged, degenerate


def _finalize(definition, blob, positions, faces, groups, prepared, budget, trial, predicted):
    error, fraction, parts, measureds = trial
    selections = []
    for raw, measured in zip(parts, measureds):
        selected = array('I')
        selected.frombytes(raw)
        selections.append((selected, measured))
    output, max_error, unchanged, degenerate = _faces_of(positions, prepared, selections)
    packed, data, stats = _pack(definition, positions, output, quantize_shading=True,
                                shading_step=1e-3, quantize_uv=True)
    if len(data) != predicted:
        raise NativeMismatch(f'native size {predicted} != packed size {len(data)}')
    stats.update(before_blob_bytes=len(blob), before_triangles=len(faces),
                 budget_met=len(data) <= budget, method='meshoptimizer_v1.2_attributes',
                 metric_error=max_error, error_limit_m=error, ratio_requested=fraction,
                 components_before=len(groups), components_after=len(components(output)),
                 preserved_extent_anchors=True, components_kept_original=unchanged,
                 degenerate_triangles_removed=degenerate,
                 shading_quantization_step=1e-3, uv_quantization_step=1e-5)
    return packed, data, stats


def simplify_meshopt_fast(definition, blob, budget, cancel=None):
    """Same return value as meshopt._simplify_meshopt_reference, or None when the native path cannot be used."""
    try:
        native = NativeRuntime()
    except NativeUnavailable:
        return None
    positions, faces = _decode(definition, blob)
    groups = components(faces)
    prepared = _prepare(positions, groups)
    try:
        sel_ptr, lens_ptr = _build_evaluator(native, definition, positions, prepared)
    except NativeUnavailable:
        return None
    runtime = Runtime()
    ex, store = runtime.exports, runtime.store
    te = ex['sbrk'](store, 4)
    uploads = [None if len(indices) <= 12 else _Group(runtime, indices, vectors, attrs, weights, locks)
               for (_, _, indices, vectors, attrs, weights, locks) in prepared]
    constant = [array('I', indices).tobytes() if len(indices) <= 12 else None
                for (_, _, indices, *_) in prepared]
    memory = native.ex['memory']
    best = None  # (size, trial)
    for error in _ERRORS:
        for fraction in _FRACTIONS:
            if _cancel(cancel):
                raise InterruptedError('LOD生成を取り消しました。')
            parts = []
            measureds = []
            lens = array('I')
            for gi, (_, _, indices, *_) in enumerate(prepared):
                if _cancel(cancel):
                    raise InterruptedError('LOD生成を取り消しました。')
                if uploads[gi] is None:
                    raw, measured = constant[gi], 0.
                else:
                    target = max(3, int(len(indices) * fraction) // 3 * 3)
                    raw, measured = _simplify_group(runtime, te, uploads[gi], target, error)
                parts.append(raw)
                measureds.append(measured)
                lens.append(len(raw) // 4)
            joined = b''.join(parts)
            memory.write(native.store, joined, sel_ptr)
            memory.write(native.store, lens.tobytes(), lens_ptr)
            size = native.call('tp_size', sel_ptr, len(joined) // 4, lens_ptr)
            if size < 0:
                return None
            trial = (error, fraction, parts, measureds)
            if best is None or size < best[0]:
                best = (size, trial)
            if size <= budget:
                return _finalize(definition, blob, positions, faces, groups, prepared, budget, trial, size)
    return _finalize(definition, blob, positions, faces, groups, prepared, budget, best[1], best[0])
