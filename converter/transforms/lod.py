"""Generate bounded, independent distant meshes; never truncate face lists.

The TPF3 game reports a 256 KiB lowest-LOD mesh-data ceiling. Source TPF2
fixtures reuse their detailed body mesh as the last static LOD. This module
first compacts exact attribute duplicates, then contracts legal mesh edges.
Material boundaries, texture seams, non-manifold topology and curved outlines
are protected. Original close-distance resources are never overwritten.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import heapq
import math
import os
from pathlib import Path
import struct
import sys
import time

from converter.parsers.lua import parse, dumps

BUDGET_BYTES = 256 * 1024
TARGET_BYTES = 240 * 1024
# Bound Python topology work before trying the existing native fallback.
MAX_CONTRACTION_WORK = 250_000
# Windows allows at most 61 worker processes in one executor
MAX_LOD_WORKERS = 61


def _array(value):
    return [value[k] for k in sorted(k for k in value if type(k) is int)] if isinstance(value, dict) else list(value)


def _dot(a, b):
    return sum(x*y for x,y in zip(a,b))


def _sub(a, b):
    return tuple(x-y for x,y in zip(a,b))


def _cross(a, b):
    return (a[1]*b[2]-a[2]*b[1], a[2]*b[0]-a[0]*b[2], a[0]*b[1]-a[1]*b[0])


def _normal(points):
    n = _cross(_sub(points[1],points[0]), _sub(points[2],points[0]))
    length = math.sqrt(_dot(n,n))
    return tuple(x/length for x in n) if length > 1e-14 else None


def _cancel(cancel):
    return cancel is not None and (cancel.is_set() if hasattr(cancel, 'is_set') else cancel())


def _nodes(node):
    yield node
    for child in _array(node.get('children', {})):
        yield from _nodes(child)


def _decode(definition, blob):
    attrs = {}
    for name, spec in definition['vertexAttr'].items():
        comps = spec['numComp']
        begin, size = spec['offset'], spec['count']
        if comps < 1 or begin < 0 or size % (4*comps) or begin+size > len(blob):
            raise ValueError(f'Invalid attribute range: {name}')
        attrs[name] = list(struct.iter_unpack('<'+'f'*comps, blob[begin:begin+size]))
    if 'position' not in attrs or definition['vertexAttr']['position']['numComp'] != 3:
        raise ValueError('Three-component positions are required for distant LOD generation')
    if any(name not in {'position', 'normal', 'tangent', 'uv0', 'uv1'} for name in attrs):
        raise ValueError('Skinned or unknown attributes cannot be simplified automatically')
    positions = attrs['position']
    faces = []
    for sub_id, sub in enumerate(_array(definition['subMeshes'])):
        streams = {}
        for name, spec in sub['indices'].items():
            begin, size = spec['offset'], spec['count']
            if name not in attrs or size % 12 or begin < 0 or begin+size > len(blob):
                raise ValueError(f'Invalid triangle indices: {name}')
            streams[name] = [x[0] for x in struct.iter_unpack('<I', blob[begin:begin+size])]
        if 'position' not in streams or len({len(v) for v in streams.values()}) != 1:
            raise ValueError('Position and attribute index stream lengths differ')
        for corner in range(0, len(streams['position']), 3):
            ids = list(streams['position'][corner:corner+3])
            values = [{name: attrs[name][stream[corner+j]] for name,stream in streams.items()} for j in range(3)]
            points = [positions[v] for v in ids]
            normal = _normal(points)
            if len(set(ids)) < 3 or normal is None:
                continue  # zero-area triangle: invisible, and it would break edge contraction
            faces.append({'ids': ids, 'attrs': values, 'sub': sub_id, 'normal': normal})
    return positions, faces


def _pack(definition, positions, faces, quantize_shading=False, shading_step=1e-5, quantize_uv=False):
    names = list(definition['vertexAttr'])
    pools = {name: [] for name in names}
    lookups = {name: {} for name in names}
    streams = [{name: [] for name in sub['indices']} for sub in _array(definition['subMeshes'])]
    corners = [set() for _ in streams]
    triangles = 0
    for face in faces:
        if face is None:
            continue
        triangles += 1
        for vid, values in zip(face['ids'], face['attrs']):
            vertex_key = []
            for name in streams[face['sub']]:
                value = positions[vid] if name == 'position' else values[name]
                if quantize_shading and name in {'normal','tangent'} and all(math.isfinite(v) for v in value):
                    # The source exporter stores a distinct tangent for nearly
                    # every face corner, often differing only by float noise.
                    # Round XYZ to 1e-5 and normalize; tangent handedness stays
                    # byte-exact. This is under 0.001 degrees for unit vectors.
                    xyz=tuple(round(v,round(-math.log10(shading_step))) for v in value[:3])
                    length=math.sqrt(_dot(xyz,xyz))
                    if length>1e-12:
                        value=tuple(v/length for v in xyz)+tuple(value[3:])
                if quantize_uv and name in {'uv0','uv1'}:
                    value=tuple(round(v,5) for v in value)
                raw = struct.pack('<'+'f'*len(value), *value)
                key = lookups[name].get(raw)
                if key is None:
                    key = len(pools[name]); lookups[name][raw] = key; pools[name].append(raw)
                streams[face['sub']][name].append(key)
                vertex_key.append((name,raw))
            corners[face['sub']].add(tuple(vertex_key))
    result = deepcopy(definition)
    blob = bytearray()
    for name in names:
        raw = b''.join(pools[name])
        result['vertexAttr'][name].update(offset=len(blob),count=len(raw))
        blob.extend(raw)
    cached_streams = {}
    for sub_id, sub in enumerate(_array(result['subMeshes'])):
        for name, ids in streams[sub_id].items():
            raw = struct.pack('<'+'I'*len(ids), *ids)
            if raw in cached_streams:
                offset = cached_streams[raw]
            else:
                offset = len(blob); cached_streams[raw] = offset; blob.extend(raw)
            sub['indices'][name].update(offset=offset,count=len(raw))
    # Native vertex stride is not documented. 64 bytes is conservative for the
    # supported position/normal/tangent/2UV profile; use actual stride if larger.
    strides = [max(64, sum(definition['vertexAttr'][name]['numComp']*4 for name in s)) for s in streams]
    gpu_bytes = sum(len(corners[i])*strides[i] + len(streams[i]['position'])*4 for i in range(len(streams)))
    return result, bytes(blob), {'triangles':triangles, 'blob_bytes':len(blob), 'render_vertices':sum(map(len,corners)), 'render_bytes':gpu_bytes, 'budget_bytes':len(blob), 'render_stride_profile':strides, 'budget_basis':'raw mesh data (game-reported fixture bytes)'}


def mesh_budget(definition, blob) -> dict:
    """Return raw and seam-aware 64-byte-stride memory estimates."""
    positions, faces = _decode(definition, blob)
    _, _, stats = _pack(definition, positions, faces)
    stats['blob_bytes'] = len(blob)
    stats['budget_bytes'] = len(blob)
    return stats


def _uv_gradient(face, positions, name):
    cached = face.setdefault('_uv_gradients', {})
    if name in cached:
        return cached[name]
    p = [positions[v] for v in face['ids']]
    edge1, edge2 = _sub(p[1],p[0]), _sub(p[2],p[0])
    aa, ab, bb = _dot(edge1,edge1), _dot(edge1,edge2), _dot(edge2,edge2)
    det = aa*bb-ab*ab
    if det <= 1e-20:
        return None
    uv = [v[name] for v in face['attrs']]
    gradients = []
    for comp in range(len(uv[0])):
        du, dv = uv[1][comp]-uv[0][comp], uv[2][comp]-uv[0][comp]
        a, b = (du*bb-dv*ab)/det, (dv*aa-du*ab)/det
        gradients.append(tuple(a*x+b*y for x,y in zip(edge1,edge2)))
    cached[name] = gradients
    return gradients


def simplify_mesh(definition, blob: bytes, budget=TARGET_BYTES, cancel=None, max_work=None):
    """Edge contraction with topology, outline, UV and accumulated-plane limits.

    Returns (definition, blob, statistics). A result exceeding budget is
    explicitly marked budget_met=False and must not be declared a valid MOD.
    """
    positions, faces = _decode(definition,blob)
    original_faces = len(faces)
    packed, packed_blob, stats = _pack(definition,positions,faces,quantize_shading=True)
    if not faces:
        # A zero-area helper still carries an animation/bogie frame. Packing
        # no faces creates an empty .blob that the game refuses to load.
        stats.update(blob_bytes=len(blob), budget_bytes=len(blob),
                     before_blob_bytes=len(blob), before_triangles=0, collapses=0,
                     budget_met=len(blob) <= budget, max_plane_error=0.,
                     max_uv_change=0., empty_geometry=True)
        return deepcopy(definition), blob, stats
    if stats['budget_bytes'] <= budget:
        stats.update(before_blob_bytes=len(blob),before_triangles=original_faces,collapses=0,budget_met=True,max_plane_error=0.,max_uv_change=0.)
        return packed, packed_blob, stats
    incident = [set() for _ in positions]
    planes = [set() for _ in positions]
    for fid, face in enumerate(faces):
        normal = face['normal']; distance = _dot(normal,positions[face['ids'][0]])
        plane = tuple(round(v,10) for v in (*normal,distance))
        for vid in face['ids']:
            incident[vid].add(fid); planes[vid].add(plane)
    # A legal boundary contraction can erase a microscopic disconnected
    # triangle. Preserve every material/topology component, however small.
    component_parent=list(range(len(faces)))
    def component_root(fid):
        while component_parent[fid]!=fid:
            component_parent[fid]=component_parent[component_parent[fid]]
            fid=component_parent[fid]
        return fid
    for face_ids in incident:
        by_material={}
        for fid in face_ids:
            sub=faces[fid]['sub']
            if sub in by_material:
                a,b=component_root(fid),component_root(by_material[sub])
                if a!=b:
                    component_parent[a]=b
            else:
                by_material[sub]=fid
    component_ids=[component_root(fid) for fid in range(len(faces))]
    component_counts={}
    for component in component_ids:
        component_counts[component]=component_counts.get(component,0)+1
    original_components=len(component_counts)
    versions = [0]*len(positions)
    active_count, collapses, max_uv = original_faces, 0, 0.
    max_error_observed = 0.
    work_used = 0

    def neighbors(vid):
        return {v for fid in incident[vid] for v in faces[fid]['ids'] if v != vid}

    def candidate(src,dst,error_limit):
        nonlocal work_used
        work_used += 1 + len(incident[src]) + len(incident[dst]) + len(planes[src])
        if max_work is not None and work_used >= max_work:
            return None
        if not incident[src] or not incident[dst]:
            return None
        shared = incident[src] & incident[dst]
        if not 1 <= len(shared) <= 2:
            return None
        deleted_components={}
        for fid in shared:
            component=component_ids[fid]
            deleted_components[component]=deleted_components.get(component,0)+1
        if any(component_counts[component]<=removed for component,removed in deleted_components.items()):
            return None
        n_src, n_dst = neighbors(src), neighbors(dst)
        opposite = {v for f in shared for v in faces[f]['ids'] if v not in {src,dst}}
        if n_src & n_dst != opposite:
            return None  # link condition: prevents holes, pinching and non-manifold seams
        if len({faces[f]['sub'] for f in incident[src] | incident[dst]}) != 1:
            return None
        boundary = [v for v in n_src if len(incident[src] & incident[v]) == 1]
        if boundary:
            if len(boundary) != 2 or dst not in boundary:
                return None
            a,b = (positions[v] for v in boundary)
            segment = _sub(b,a); length2 = _dot(segment,segment)
            if not length2:
                return None
            t = _dot(_sub(positions[src],a),segment)/length2
            projected = tuple(x+t*y for x,y in zip(a,segment))
            if not 0 <= t <= 1 or math.sqrt(_dot(_sub(positions[src],projected),_sub(positions[src],projected))) > min(error_limit,0.002):
                return None  # retain outlines and holes within two millimetres
        error = 0.
        for plane in planes[src]:
            deviation = abs(_dot(plane[:3],positions[dst])-plane[3])
            if deviation > error_limit:
                return None
            error = max(error, deviation)
        uv_names = [name for name in ('uv0','uv1') if name in faces[next(iter(incident[src]))]['attrs'][0]]
        for name in uv_names:
            groups = {}
            edge_uv = []
            delta = _sub(positions[dst],positions[src])
            for fid in incident[src]:
                face = faces[fid]; corner = face['ids'].index(src)
                uv = face['attrs'][corner][name]
                gradient = _uv_gradient(face,positions,name)
                if gradient is None:
                    return None
                projected_uv = tuple(value+_dot(g,delta) for value,g in zip(uv,gradient))
                key=tuple(round(v,5) for v in uv)
                groups.setdefault(key,[]).append(projected_uv)
                if dst in face['ids']:
                    edge_uv.append((key,face['attrs'][face['ids'].index(dst)][name]))
            # UV seam vertices may contract ALONG a seam when every island has
            # a matching edge endpoint. Islands never merge across the seam.
            for key,projected_values in groups.items():
                endpoints=[uv for island,uv in edge_uv if island==key]
                if not endpoints:
                    return None
                expected=endpoints[0]
                if any(any(abs(a-b)>.0002 for a,b in zip(expected,uv)) for uv in projected_values+endpoints):
                    return None  # directional affine mismatch / texture boundary
        for fid in incident[src]-shared:
            face = faces[fid]
            new_points = [positions[dst if v==src else v] for v in face['ids']]
            normal = _normal(new_points)
            if normal is None or _dot(normal,face['normal']) < math.cos(math.radians(8)):
                return None
        return error

    # Begin with shape-preserving planar operations. Subsequent stages have a
    # strict distant-surface tolerance, accumulated against original planes.
    for error_limit in (0.00001, .002, .01, .03):
        heap=[]
        def enqueue(src):
            if not incident[src]:
                return
            for dst in neighbors(src):
                error = candidate(src,dst,error_limit)
                if error is not None:
                    length2 = _dot(_sub(positions[src],positions[dst]),_sub(positions[src],positions[dst]))
                    heapq.heappush(heap,(error*error+length2*1e-9,src,dst,versions[src],versions[dst]))
        for vid in range(len(positions)):
            if _cancel(cancel):
                raise InterruptedError('最低LODの生成を取り消しました。')
            enqueue(vid)
            if max_work is not None and work_used >= max_work:
                break
        since_pack = 0
        while heap and (max_work is None or work_used < max_work):
            if _cancel(cancel):
                raise InterruptedError('最低LODの生成を取り消しました。')
            _,src,dst,sv,dv=heapq.heappop(heap)
            if versions[src]!=sv or versions[dst]!=dv:
                continue
            error = candidate(src,dst,error_limit)
            if error is None:
                continue
            changed = incident[src] | incident[dst]
            touched = {v for fid in changed for v in faces[fid]['ids']}
            shared = incident[src] & incident[dst]
            for fid in list(incident[src]):
                face = faces[fid]
                if fid in shared:
                    for v in face['ids']:
                        incident[v].discard(fid)
                    component_counts[component_ids[fid]]-=1
                    faces[fid]=None; active_count-=1
                    continue
                corner = face['ids'].index(src)
                delta = _sub(positions[dst],positions[src])
                for name in ('uv0','uv1'):
                    if name in face['attrs'][corner]:
                        gradient = _uv_gradient(face,positions,name)
                        uv = face['attrs'][corner][name]
                        new_uv = tuple(value+_dot(g,delta) for value,g in zip(uv,gradient))
                        max_uv=max(max_uv,max(abs(a-b) for a,b in zip(uv,new_uv)))
                        face['attrs'][corner][name]=new_uv
                face['ids'][corner]=dst
                face['normal']=_normal([positions[v] for v in face['ids']])
                face.pop('_uv_gradients', None)
                incident[dst].add(fid)
            incident[src].clear()
            planes[dst].update(planes[src]); planes[src].clear()
            collapses+=1; since_pack+=1; max_error_observed=max(max_error_observed,error)
            for vid in touched:
                versions[vid]+=1
            for vid in touched:
                enqueue(vid)
            if since_pack>=60:
                packed,packed_blob,stats=_pack(definition,positions,faces,quantize_shading=True); since_pack=0
                if stats['budget_bytes']<=budget:
                    stats.update(before_blob_bytes=len(blob),before_triangles=original_faces,collapses=collapses,budget_met=True,max_plane_error=max_error_observed,max_uv_change=max_uv,components_before=original_components,components_after=sum(v>0 for v in component_counts.values()),shading_quantization_step=1e-5,outline_tolerance_m=.002)
                    return packed,packed_blob,stats
        packed,packed_blob,stats=_pack(definition,positions,faces,quantize_shading=True)
        if stats['budget_bytes']<=budget:
            break
        if max_work is not None and work_used >= max_work:
            break
    stats.update(before_blob_bytes=len(blob),before_triangles=original_faces,collapses=collapses,budget_met=stats['budget_bytes']<=budget,max_plane_error=max_error_observed,max_uv_change=max_uv,components_before=original_components,components_after=sum(v>0 for v in component_counts.values()),shading_quantization_step=1e-5,outline_tolerance_m=.002)
    stats.update(contraction_work=work_used, contraction_work_limit=max_work,
                 contraction_work_limit_reached=max_work is not None and work_used >= max_work)
    return packed,packed_blob,stats


def _local(content,owner,ref):
    if not isinstance(ref,str) or '::' in ref:
        return None
    path = (content/ref.lstrip('/')) if ref.startswith('/') else owner.parent/ref
    resolved=path.resolve()
    if not resolved.is_relative_to(content.resolve()):
        return None
    return resolved


class _MeshCache:
    """Each distinct mesh file is parsed and measured once per run: the cars of a train share most of their meshes."""

    def __init__(self, keep_data=True):
        self.keep_data = keep_data
        self.items = {}

    def get(self, path):
        """-> (definition, blob, stats); the data is None when keep_data is off. Re-raises the error of an unreadable mesh."""
        key = str(path)
        if key not in self.items:
            try:
                definition = parse(path.read_text(encoding='utf-8-sig'), key)
                blob = Path(key + '.blob').read_bytes()
                stats = mesh_budget(definition, blob)
                self.items[key] = (definition, blob, stats, None) if self.keep_data else (None, None, stats, None)
            except (ValueError, OSError) as exc:
                self.items[key] = (None, None, None, exc)
        definition, blob, stats, error = self.items[key]
        if error is not None:
            raise error
        return definition, blob, stats


def validate_lowest_lods(package: Path, parsed=None, cache=None):
    diagnostics=[]; content=Path(package)/'content'
    cache=cache or _MeshCache(keep_data=False)
    models = [(content/p,d) for p,d in (parsed or {}).items() if p.endswith('.mdl')]
    if not models:
        models=[(p,parse(p.read_text(encoding='utf-8-sig'),str(p))) for p in content.rglob('*.mdl')]
    for owner,model in models:
        lods=_array(model.get('lods',{}))
        if not lods:
            continue
        totals={'blob_bytes':0,'render_bytes':0,'budget_bytes':0}; details=[]
        for node in _nodes(lods[-1].get('node',{})):
            if not node.get('mesh'):
                continue
            if isinstance(node['mesh'],str) and node['mesh'].startswith('::/'):
                continue  # a mesh TPF3 ships (placeholder cube etc.): nothing of ours to measure
            path=_local(content,owner,node['mesh'])
            if path is None or not path.is_file():
                diagnostics.append({'severity':'warning','code':'LOWEST_LOD_UNVERIFIED','message':'最遠景LODの外部または欠損メッシュのサイズを確認できません。','file':str(owner),'reference':node['mesh']}); continue
            try:
                stats=cache.get(path)[2]
            except (ValueError,OSError) as exc:
                diagnostics.append({'severity':'warning','code':'LOWEST_LOD_UNVERIFIED','message':f'最遠景LODを検査できません: {exc}','file':str(owner)}); continue
            for key in totals:
                totals[key]+=stats[key]
            details.append({'mesh':node['mesh'],**stats})
        if totals['budget_bytes']>BUDGET_BYTES:
            diagnostics.append({'severity':'warning','code':'LOWEST_LOD_BUDGET','message':f'最遠景LODのメッシュ合計が上限256KiBを超えています: {totals["budget_bytes"]/1024:.1f}KiB。（TPF3の検証では警告で、読み込みも表示もできます。遠景の描画負荷が大きくなります）','file':str(owner),'limit_bytes':BUDGET_BYTES,'totals':totals,'meshes':details})
    return diagnostics


_worker_cancel = None


def _trace(message):
    """Set TPF3_LOD_TRACE=1 to print how long each distinct mesh takes (where a slow conversion spends its time)."""
    if os.environ.get('TPF3_LOD_TRACE'):
        print('[lod]', message, file=sys.stderr, flush=True)  # stderr: the CLI keeps stdout for its JSON report


def _init_lod_worker(cancel):
    global _worker_cancel
    _worker_cancel = cancel


def _simplify_with_fallback(definition, blob, allocation, cancel):
    result = simplify_mesh(definition, blob, allocation, cancel, max_work=MAX_CONTRACTION_WORK)
    if result[2].get('empty_geometry'):
        return result
    if not result[2]['budget_met']:
        from converter.transforms.meshopt import simplify_meshopt
        fallback = simplify_meshopt(definition, blob, allocation, cancel)
        if len(fallback[1]) < len(result[1]):
            result = fallback
    return result


def _run_simplify_job(job):
    """Worker side: the mesh is read from disk again, so only the path and the byte budget cross the process boundary."""
    path, allocation = job
    path = Path(path)
    definition = parse(path.read_text(encoding='utf-8-sig'), str(path))
    blob = Path(str(path) + '.blob').read_bytes()
    started = time.perf_counter()
    result = _simplify_with_fallback(definition, blob, allocation, _worker_cancel)
    _trace(f'job {path.name} budget {allocation} bytes: {time.perf_counter() - started:.1f}s')
    return result


def _simplify_all(jobs, workers, progress, cancel):
    """jobs: {(path, allocation): (definition, blob, cost)} -> {(path, allocation): simplify result}.

    The results do not depend on each other or on the order, so the output is identical for any worker count.
    The costliest meshes go first so that the last worker is not left with one huge mesh.
    """
    results = {}
    order = sorted(jobs, key=lambda key: -jobs[key][2])
    total = len(order)

    def report(done, key):
        if progress:
            progress({'stage': 'optimize_lod', 'current': done, 'total': total, 'message': f'{Path(key[0]).name}（メッシュ簡略化 {done}/{total}）'})

    workers = min(workers, total, MAX_LOD_WORKERS)
    _trace(f'{total} distinct meshes to simplify, workers {workers}, original sizes (KiB): ' + ', '.join(f'{jobs[k][2] / 1024:.0f}' for k in order[:12]))
    if workers <= 1:
        for done, key in enumerate(order, 1):
            if _cancel(cancel):
                raise InterruptedError('最低LODの生成を取り消しました。')
            definition, blob, _ = jobs[key]
            results[key] = _simplify_with_fallback(deepcopy(definition), blob, key[1], cancel)
            report(done, key)
        return results

    import multiprocessing
    from concurrent.futures import ProcessPoolExecutor, wait, FIRST_COMPLETED
    context = multiprocessing.get_context('spawn')  # works on Windows, in GUI worker threads and in the frozen executable
    stop = context.Event()
    pending, next_index, done = {}, 0, 0
    pool = ProcessPoolExecutor(max_workers=workers, mp_context=context, initializer=_init_lod_worker, initargs=(stop,))
    try:
        while next_index < total or pending:
            if _cancel(cancel):
                raise InterruptedError('最低LODの生成を取り消しました。')
            # keep the queue short so that cancelling does not have to drain a long backlog
            while next_index < total and len(pending) < workers:
                key = order[next_index]
                pending[pool.submit(_run_simplify_job, key)] = key
                next_index += 1
            finished, _ = wait(pending, timeout=0.1, return_when=FIRST_COMPLETED)
            for future in finished:
                key = pending.pop(future)
                results[key] = future.result()
                done += 1
                report(done, key)
    finally:
        stop.set()
        pool.shutdown(wait=True, cancel_futures=True)
    return results


def optimize_lowest_lods(package: Path, progress=None, cancel=None, workers=1):
    """Create derived assets only after a whole lowest LOD fits its budget.

    Three passes: (1) per model, decide which meshes must be simplified and to what size; (2) simplify every distinct
    (mesh, size) once, on `workers` processes; (3) per model, write the results. Models share meshes (cars of one
    train), and the contraction is by far the slowest step, so pass 2 is the only one that is parallel.
    """
    package=Path(package); content=package/'content'; diagnostics=[]
    models=sorted(content.rglob('*.mdl'))
    prepared=[]; jobs={}; clock=time.perf_counter(); marks=[]; meshes=_MeshCache()
    for model_index,owner in enumerate(models):
        if _cancel(cancel):
            raise InterruptedError('最低LODの生成を取り消しました。')
        if progress:
            progress({'stage':'optimize_lod','current':model_index+1,'total':len(models),'message':owner.name})
        model=parse(owner.read_text(encoding='utf-8-sig'),str(owner))
        lods=_array(model.get('lods',{}))
        if len(lods)<2:
            continue
        flat=list(_nodes(lods[-1].get('node',{})))
        axle_names=set(_array(model.get('metadata',{}).get('railVehicle',{}).get('config',{}).get('axles',{})))
        protected=set()
        def protect_branch(node,ancestor_motion=False):
            children=_array(node.get('children',{}))
            axle_parent=any(child.get('name') in axle_names for child in children)
            own_motion=node.get('name') in axle_names or axle_parent or bool(node.get('animations')) or bool(node.get('skin'))
            child_motion=[protect_branch(c,ancestor_motion or own_motion) for c in children]
            descendant_motion=any(child_motion)
            if own_motion or ancestor_motion or descendant_motion:
                protected.add(id(node))
            return own_motion or descendant_motion
        protect_branch(lods[-1].get('node',{}))
        resources=[]; total=0; entry_diagnostics=[]
        for node in flat:
            if not node.get('mesh'):
                continue
            path=_local(content,owner,node['mesh'])
            if path is None or not path.is_file():
                continue
            try:
                definition,blob,stats=meshes.get(path)
                resources.append((node,path,definition,blob,stats)); total+=stats['budget_bytes']
            except (ValueError,OSError) as exc:
                entry_diagnostics.append({'severity':'warning','code':'LOWEST_LOD_UNVERIFIED','message':str(exc),'file':str(owner)})
        entry={'owner':owner,'model':model,'diagnostics':entry_diagnostics,'work':None}
        prepared.append(entry)
        if total<=TARGET_BYTES:
            continue
        protected_bytes=sum(s['budget_bytes'] for n,p,d,b,s in resources if id(n) in protected)
        eligible=[item for item in resources if id(item[0]) not in protected]
        if not eligible or protected_bytes>=TARGET_BYTES:
            entry_diagnostics.append({'severity':'warning','code':'LOWEST_LOD_MOVABLE','message':'最遠景LODの可動部品を保持したまま256KiB以内にできません。LODを手動で用意してください。（TPF3の検証では警告で、読み込みも表示もできます。遠景の描画負荷が大きくなります）','file':str(owner),'protected_bytes':protected_bytes}); continue
        remaining=TARGET_BYTES-protected_bytes
        original_eligible=sum(item[-1]['budget_bytes'] for item in eligible)
        planned=[]
        for node,path,definition,blob,stats in eligible:
            allocation=max(1024,int(remaining*stats['budget_bytes']/original_eligible))
            key=(str(path),allocation)
            if key not in jobs:
                jobs[key]=(definition,blob,stats['budget_bytes'])
            planned.append((node,path,definition,blob,stats,key))
        entry['work']={'total':total,'protected_bytes':protected_bytes,'planned':planned}
    marks.append(('plan',time.perf_counter()-clock)); clock=time.perf_counter()
    cache=_simplify_all(jobs,workers,progress,cancel)
    marks.append(('simplify',time.perf_counter()-clock)); clock=time.perf_counter()
    for entry in prepared:
        owner,model,work=entry['owner'],entry['model'],entry['work']
        diagnostics.extend(entry['diagnostics'])
        if work is None:
            continue
        total,protected_bytes=work['total'],work['protected_bytes']
        replacements=[]; after=protected_bytes; failed=False
        for node,path,definition,blob,stats,key in work['planned']:
            new_definition,new_blob,new_stats=cache[key]
            if not new_stats['budget_met']:
                failed=True
            after+=new_stats['budget_bytes']
            replacements.append((node,path,new_definition,new_blob,new_stats))
        if failed or after>TARGET_BYTES:
            diagnostics.append({'severity':'warning','code':'LOWEST_LOD_SIMPLIFICATION_LIMIT','message':f'形状・UV・可動部品の保全条件の下では最遠景LODを軽量化しきれません: {after/1024:.1f}KiB。（TPF3の検証では警告で、読み込みも表示もできます。遠景の描画負荷が大きくなります）','file':str(owner),'before_bytes':total,'after_bytes':after,'limit_bytes':BUDGET_BYTES}); continue
        records=[]
        for node,path,definition,blob,stats in replacements:
            digest=hashlib.sha256(dumps(definition).encode('utf-8')+blob).hexdigest()[:12]
            target=content/'_assets'/'generated_lod'/f'{path.stem}_{digest}.msh'
            target.parent.mkdir(parents=True,exist_ok=True)
            target.write_text(dumps(definition),encoding='utf-8'); Path(str(target)+'.blob').write_bytes(blob)
            node['mesh']='/'+target.relative_to(content).as_posix()
            records.append({'source_mesh':path.relative_to(content).as_posix(),'generated_mesh':node['mesh'],**stats})
        owner.write_text(dumps(model),encoding='utf-8')
        diagnostics.append({'severity':'info','code':'LOWEST_LOD_OPTIMIZED','message':f'最遠景LODを{total/1024:.1f}KiBから{after/1024:.1f}KiBへ軽量化しました。近景・ドア・台車は元データを保持しています。','file':str(owner),'before_bytes':total,'after_bytes':after,'limit_bytes':BUDGET_BYTES,'meshes':records})
    marks.append(('write',time.perf_counter()-clock)); clock=time.perf_counter()
    diagnostics.extend(validate_lowest_lods(package,cache=meshes))
    marks.append(('validate',time.perf_counter()-clock))
    _trace('LOD stage seconds: '+', '.join(f'{name} {seconds:.1f}' for name,seconds in marks))
    return diagnostics
