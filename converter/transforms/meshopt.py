"""Attribute-aware distant LOD fallback, meshoptimizer v1.2 (MIT).

The pinned WASM has no imports or filesystem access. Each material/topology
component is simplified independently; no pruning flag or random face removal.
Returned vertices retain their original position, UV and shading attributes.
"""
from pathlib import Path
import hashlib
import math
import struct

from .lod import _decode, _pack, _normal, _cancel

WASM_SHA256 = '0c1c462dc0bcd02bfea0e45cf8f76d56875c584ed4dcc8e5296a44972400f7cd'
_module = None
_engine = None

class Runtime:
    def __init__(self):
        import wasmtime
        global _module, _engine
        if _module is None:
            wasm = (Path(__file__).parents[1]/'vendor/meshoptimizer_v1_2.wasm').read_bytes()
            if hashlib.sha256(wasm).hexdigest() != WASM_SHA256:
                raise ValueError('meshoptimizer binary integrity check failed')
            _engine = wasmtime.Engine()
            _module = wasmtime.Module(_engine, wasm)
            if _module.imports:
                raise ValueError('meshoptimizer must not import host capabilities')
        self.store = wasmtime.Store(_engine)
        self.store.set_limits(memory_size=256*1024*1024)
        self.exports = wasmtime.Instance(self.store, _module, []).exports(self.store)
        self.exports['__wasm_call_ctors'](self.store)

    def simplify(self, indices, positions, attributes, weights, locks, target, error):
        ex, store = self.exports, self.store
        memory, sbrk = ex['memory'], ex['sbrk']
        base = sbrk(store, 0)
        def allocate(data):
            at = sbrk(store, len(data))
            memory.write(store, data, at)
            return at
        try:
            count = len(indices)
            stride = len(weights)
            te = allocate(b'\0'*4)
            ti = allocate(b'\0'*(count*4))
            sp = allocate(struct.pack('<'+'f'*len(positions), *positions))
            sa = allocate(struct.pack('<'+'f'*len(attributes), *attributes))
            sw = allocate(struct.pack('<'+'f'*len(weights), *weights))
            si = allocate(struct.pack('<'+'I'*count, *indices))
            vl = allocate(bytes(locks))
            # ErrorAbsolute + Permissive: account for attribute error without
            # permanently locking every UV seam. Prune is deliberately absent.
            result = ex['meshopt_simplifyWithAttributes'](store,ti,si,count,sp,len(locks),12,
                sa,stride*4,sw,stride,vl,target,error,4|32,te)
            output = list(struct.unpack('<'+'I'*result, memory.read(store,ti,ti+result*4)))
            measured = struct.unpack('<f',memory.read(store,te,te+4))[0]
            if result%3 or any(v>=len(locks) for v in output) or not math.isfinite(measured):
                raise ValueError('Invalid meshoptimizer output')
            return output, measured
        finally:
            sbrk(store,base-sbrk(store,0))

def components(faces):
    parent=list(range(len(faces)))
    def find(i):
        while parent[i]!=i:
            parent[i]=parent[parent[i]];i=parent[i]
        return i
    seen={}
    for i,face in enumerate(faces):
        for vid in face['ids']:
            key=(face['sub'],vid)
            if key in seen:
                a,b=find(i),find(seen[key])
                if a!=b: parent[a]=b
            seen[key]=i
    groups={}
    for i,face in enumerate(faces): groups.setdefault(find(i),[]).append(face)
    return list(groups.values())

def _prepare(positions, groups):
    """Per material/topology component: corner table, simplifier inputs and extent anchors."""
    prepared=[]
    for group in groups:
        lookups={}; corners=[]; indices=[]
        uv_names=[n for n in ('uv0','uv1') if n in group[0]['attrs'][0]]
        weights=[2.]* (2*len(uv_names)) + [0.01]*3
        vectors=[]; attrs=[]
        for face in group:
            for vid,values in zip(face['ids'],face['attrs']):
                key=(vid,tuple((n,tuple(v)) for n,v in sorted(values.items())))
                if key not in lookups:
                    lookups[key]=len(corners);corners.append((vid,values))
                    vectors.extend(positions[vid])
                    for name in uv_names: attrs.extend(values[name])
                    normal=values.get('normal',face['normal'])
                    attrs.extend(normal[:3])
                indices.append(lookups[key])
        locks=[0]*len(corners)
        # Keep six extent anchors per component, including all seam variants.
        anchored=set()
        for axis in range(3):
            for select in (min,max):
                c=select(range(len(corners)),key=lambda i:positions[corners[i][0]][axis])
                anchored.add(corners[c][0])
        for i,(vid,_) in enumerate(corners):
            if vid in anchored: locks[i]=1
        prepared.append((group,corners,indices,vectors,attrs,weights,locks))
    return prepared

def _simplify_meshopt_reference(definition, blob, budget, cancel=None):
    positions, faces = _decode(definition,blob)
    groups = components(faces)
    runtime = Runtime()
    prepared=_prepare(positions,groups)
    best = None
    # Larger meshes are progressively decimated until the actual packed bytes
    # fit. Error is in model metres plus weighted UV/normal terms, not a
    # Hausdorff guarantee. Topology components and extent anchors are retained.
    for error in (.01,.025,.05,.1,.15,.2):
        for fraction in (.65,.45,.3,.2,.12,.07):
            if _cancel(cancel): raise InterruptedError('LOD生成を取り消しました。')
            output=[]; max_error=0.; unchanged=0; degenerate=0
            for group,corners,indices,vectors,attrs,weights,locks in prepared:
                if _cancel(cancel): raise InterruptedError('LOD生成を取り消しました。')
                target=max(3,int(len(indices)*fraction)//3*3)
                if len(indices)<=12:
                    selected=indices;measured=0.
                else:
                    selected,measured=runtime.simplify(indices,vectors,attrs,weights,locks,target,error)
                converted=[]
                for i in range(0,len(selected),3):
                    chosen=[corners[j] for j in selected[i:i+3]]
                    ids=[v for v,a in chosen]
                    normal=_normal([positions[v] for v in ids])
                    if normal is None or len(set(ids))!=3:
                        # Collapse can produce collinear triangles. They have
                        # zero area; retain the source component if all vanish.
                        degenerate+=1
                        continue
                    converted.append({'ids':ids,'attrs':[dict(a) for v,a in chosen],
                                      'sub':group[0]['sub'],'normal':normal})
                if not converted:
                    # Preserve this component instead of allowing disappearance.
                    converted=group; unchanged+=1
                output.extend(converted);max_error=max(max_error,measured)
            packed,data,stats=_pack(definition,positions,output,quantize_shading=True,
                                    shading_step=1e-3,quantize_uv=True)
            stats.update(before_blob_bytes=len(blob),before_triangles=len(faces),
                budget_met=len(data)<=budget,method='meshoptimizer_v1.2_attributes',
                metric_error=max_error,error_limit_m=error,ratio_requested=fraction,
                components_before=len(groups),components_after=len(components(output)),
                preserved_extent_anchors=True,components_kept_original=unchanged,
                degenerate_triangles_removed=degenerate,
                shading_quantization_step=1e-3,uv_quantization_step=1e-5)
            if best is None or len(data)<len(best[1]): best=(packed,data,stats)
            if stats['budget_met']: return packed,data,stats
    return best


def simplify_meshopt(definition, blob, budget, cancel=None):
    """Distant-LOD fallback. Uses the native trial-size evaluator (meshopt_fast) when available; it returns exactly
    what _simplify_meshopt_reference returns, only faster. Any native problem falls back to the reference."""
    import os
    if not os.environ.get('TPF3_NO_NATIVE_PACK'):
        try:
            from .meshopt_fast import simplify_meshopt_fast
            result = simplify_meshopt_fast(definition, blob, budget, cancel)
        except InterruptedError:
            raise
        except Exception:
            result = None
        if result is not None:
            return result
    return _simplify_meshopt_reference(definition, blob, budget, cancel)
