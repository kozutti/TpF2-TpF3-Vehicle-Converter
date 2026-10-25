"""Native (WASM) port of ``lod.simplify_mesh``; see tools/strict_lod/INTERFACE.md.

Python keeps _decode/_pack/orchestration; the Rust core runs topology, candidate
checks, the heap and the exact packed-size budget checks. Any WASM problem falls
back to the pure-Python ``lod.simplify_mesh`` (identical result, only slower).
"""
from array import array
from pathlib import Path
import hashlib
import math

from . import lod

# SHA-256 of converter/vendor/strict_lod.wasm; update after rebuilding (tools/strict_lod/README.md).
STRICT_LOD_WASM_SHA256 = 'b00a49f31399361092c3c23f9e83ecc47b55916c0ded6f8dda54ae68abae85a4'
WASM_PATH = Path(__file__).parents[1] / 'vendor' / 'strict_lod.wasm'
MEMORY_LIMIT = 2 * 1024 * 1024 * 1024
STEP_UNITS = 2000
MAGIC = 0x4C545331
DELETED = 0xFFFFFFFF
_U64 = (1 << 64) - 1
_module = None
_engine = None
LAST_NATIVE_COMPONENTS_BEFORE = None  # diagnostic: sl_out_stats()[6] of the last native run
_broken = False   # integrity / import failure: do not retry


class NativeUnavailable(Exception):
    pass


def _load():
    """Return (engine, module) or raise NativeUnavailable."""
    global _module, _engine, _broken
    if _module is not None:
        return _engine, _module
    if _broken:
        raise NativeUnavailable('strict_lod wasm unusable')
    try:
        import wasmtime
        wasm = WASM_PATH.read_bytes()
    except Exception as exc:  # missing file / wasmtime not installed
        raise NativeUnavailable(str(exc)) from exc
    try:
        if STRICT_LOD_WASM_SHA256 is not None and hashlib.sha256(wasm).hexdigest() != STRICT_LOD_WASM_SHA256:
            raise ValueError('strict_lod binary integrity check failed')
        engine = wasmtime.Engine()
        module = wasmtime.Module(engine, wasm)
        if module.imports:
            raise ValueError('strict_lod must not import host capabilities')
    except Exception as exc:
        _broken = True
        raise NativeUnavailable(str(exc)) from exc
    _engine, _module = engine, module
    return engine, module


def native_available() -> bool:
    try:
        _load()
        return True
    except NativeUnavailable:
        return False


class Runtime:
    """One fresh wasm instance (the module exposes no reset/free)."""

    def __init__(self):
        import wasmtime
        engine, module = _load()
        self.store = wasmtime.Store(engine)
        self.store.set_limits(memory_size=MEMORY_LIMIT)
        self.ex = wasmtime.Instance(self.store, module, []).exports(self.store)

    def alloc_bytes(self, data):
        data = bytes(data)
        ptr = self.ex['sl_alloc'](self.store, max(len(data), 8))
        if data:
            self.ex['memory'].write(self.store, data, ptr)
        return ptr

    def call(self, name, *args):
        return self.ex[name](self.store, *args)

    def read(self, ptr, size):
        return self.ex['memory'].read(self.store, ptr, ptr + size)


def _components(faces):
    """(material, topology) component count; same union-find as simplify_mesh."""
    incident = {}
    for fid, face in enumerate(faces):
        for vid in face['ids']:
            incident.setdefault(vid, set()).add(fid)
    parent = list(range(len(faces)))

    def root(fid):
        while parent[fid] != fid:
            parent[fid] = parent[parent[fid]]
            fid = parent[fid]
        return fid
    for face_ids in incident.values():
        by_material = {}
        for fid in face_ids:
            sub = faces[fid]['sub']
            if sub in by_material:
                a, b = root(fid), root(by_material[sub])
                if a != b:
                    parent[a] = b
            else:
                by_material[sub] = fid
    return len({root(fid) for fid in range(len(faces))})


_CHANNELS = ('normal', 'tangent', 'uv0', 'uv1')


def _run_native(definition, positions, faces, budget, max_work, cancel):
    """Returns (status, out_faces_bytes, out_uv_bytes, stats_tuple, comps dict)."""
    comps = {}
    for name in _CHANNELS:
        spec = definition['vertexAttr'].get(name)
        comps[name] = int(spec['numComp']) if spec else 0
    subs = lod._array(definition['subMeshes'])
    nsub = len(subs)
    bits = {'normal': 2, 'tangent': 4, 'uv0': 8, 'uv1': 16}
    sub_mask = array('I', [
        1 | sum(bit for name, bit in bits.items() if name in sub['indices']) for sub in subs])
    nv, nf = len(positions), len(faces)
    stride = sum(comps.values())
    pos = array('d')
    for p in positions:
        pos.extend(p)
    ids = array('I')
    fsub = array('I')
    corner = array('d')
    zeros = {name: (0.0,) * comps[name] for name in _CHANNELS}
    for face in faces:
        ids.extend(face['ids'])
        fsub.append(face['sub'])
        for attrs in face['attrs']:
            for name in _CHANNELS:
                if comps[name]:
                    corner.extend(attrs.get(name, zeros[name]))
    if len(corner) != nf * 3 * stride:
        raise NativeUnavailable('unexpected attribute layout')

    rt = Runtime()
    p_pos, p_ids, p_sub = rt.alloc_bytes(pos.tobytes()), rt.alloc_bytes(ids.tobytes()), rt.alloc_bytes(fsub.tobytes())
    p_mask, p_corner = rt.alloc_bytes(sub_mask.tobytes()), rt.alloc_bytes(corner.tobytes())
    mw = int(max_work) if max_work is not None else 0
    mw = max(0, min(mw, _U64))
    b = budget
    b = math.floor(b) if isinstance(b, float) and math.isfinite(b) else int(b)
    b = max(0, min(b, _U64))
    header = array('I', [
        MAGIC, nv, nf, nsub, 1 if max_work is not None else 0, mw & 0xFFFFFFFF, mw >> 32,
        b & 0xFFFFFFFF, b >> 32, comps['normal'], comps['tangent'], comps['uv0'], comps['uv1'],
        p_pos, p_ids, p_sub, p_mask, p_corner])
    code = rt.call('sl_init', rt.alloc_bytes(header.tobytes()))
    if code != 0:
        raise NativeUnavailable(f'sl_init error {code}')
    while True:
        if lod._cancel(cancel):
            raise InterruptedError('最低LODの生成を取り消しました。')
        status = rt.call('sl_step', STEP_UNITS)
        if status < 0:
            raise NativeUnavailable(f'sl_step error {status}')
        if status in (1, 2):
            break
    import struct
    out_ids = rt.read(rt.call('sl_out_faces'), nf * 12)
    uv_stride = comps['uv0'] + comps['uv1']
    out_uv = rt.read(rt.call('sl_out_uv'), nf * 3 * uv_stride * 8) if uv_stride else b''
    st = struct.unpack('<8d', rt.read(rt.call('sl_out_stats'), 64))
    return status, out_ids, out_uv, st, comps


def simplify_mesh_native(definition, blob, budget=lod.TARGET_BYTES, cancel=None, max_work=None):
    """Drop-in for ``lod.simplify_mesh`` using the WASM core when available."""
    positions, faces = lod._decode(definition, blob)
    original_faces = len(faces)
    packed, packed_blob, stats = lod._pack(definition, positions, faces, quantize_shading=True)
    if stats['budget_bytes'] <= budget:
        stats.update(before_blob_bytes=len(blob), before_triangles=original_faces, collapses=0,
                     budget_met=True, max_plane_error=0., max_uv_change=0.)
        return packed, packed_blob, stats
    try:
        import struct
        _load()
        components_before = _components(faces)
        status, out_ids, out_uv, st, comps = _run_native(definition, positions, faces, budget, max_work, cancel)
        nf = len(faces)
        flat = struct.unpack(f'<{nf * 3}I', out_ids)
        uv_stride = comps['uv0'] + comps['uv1']
        uvs = struct.unpack(f'<{nf * 3 * uv_stride}d', out_uv) if uv_stride else ()
        offsets = {'uv0': 0, 'uv1': comps['uv0']}
        rebuilt = []
        for fid, face in enumerate(faces):
            base = fid * 3
            if flat[base] == DELETED:
                rebuilt.append(None)
                continue
            face['ids'] = list(flat[base:base + 3])
            for j, attrs in enumerate(face['attrs']):
                at = (base + j) * uv_stride
                for name in ('uv0', 'uv1'):
                    if name in attrs:
                        o = at + offsets[name]
                        attrs[name] = tuple(uvs[o:o + comps[name]])
            rebuilt.append(face)
        global LAST_NATIVE_COMPONENTS_BEFORE
        LAST_NATIVE_COMPONENTS_BEFORE = int(st[6])
        collapses, max_plane, max_uv, work_used, comps_after, reached = (
            int(st[0]), float(st[1]), float(st[2]), int(st[3]), int(st[4]), bool(st[5]))
        packed, packed_blob, stats = lod._pack(definition, positions, rebuilt, quantize_shading=True)
    except InterruptedError:
        raise
    except Exception:
        return lod.simplify_mesh(definition, blob, budget=budget, cancel=cancel, max_work=max_work)
    if status == 1:
        stats.update(before_blob_bytes=len(blob), before_triangles=original_faces, collapses=collapses,
                     budget_met=True, max_plane_error=max_plane, max_uv_change=max_uv,
                     components_before=components_before, components_after=comps_after,
                     shading_quantization_step=1e-5, outline_tolerance_m=.002)
        return packed, packed_blob, stats
    stats.update(before_blob_bytes=len(blob), before_triangles=original_faces, collapses=collapses,
                 budget_met=stats['budget_bytes'] <= budget, max_plane_error=max_plane, max_uv_change=max_uv,
                 components_before=components_before, components_after=comps_after,
                 shading_quantization_step=1e-5, outline_tolerance_m=.002)
    stats.update(contraction_work=work_used, contraction_work_limit=max_work,
                 contraction_work_limit_reached=max_work is not None and reached)
    return packed, packed_blob, stats
