from copy import deepcopy
import hashlib
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch

from converter.parsers.lua import parse, dumps
from converter.codecs.mesh import validate_mesh
from converter.transforms.lod import (
    _decode, _pack, mesh_budget, optimize_lowest_lods,
    simplify_mesh, validate_lowest_lods,
)


def grid_mesh(side=10):
    positions=[(float(x),float(y),0.) for y in range(side+1) for x in range(side+1)]
    specs={'position':{'numComp':3},'normal':{'numComp':3},'tangent':{'numComp':4},'uv0':{'numComp':2}}
    definition={'vertexAttr':specs,'subMeshes':{1:{'indices':{name:{} for name in specs}}}}
    faces=[]
    for y in range(side):
        for x in range(side):
            a=y*(side+1)+x; b=a+1; c=a+side+1; d=c+1
            for ids in ((a,b,d),(a,d,c)):
                attrs=[]
                for vid in ids:
                    p=positions[vid]
                    attrs.append({'position':p,'normal':(0.,0.,1.),'tangent':(1.,0.,0.,-1.),'uv0':(p[0]/side,p[1]/side)})
                faces.append({'ids':list(ids),'attrs':attrs,'sub':0,'normal':(0.,0.,1.)})
    definition,blob,_=_pack(definition,positions,faces)
    return definition,blob


class LodTests(unittest.TestCase):
    def test_work_limit_preserves_mesh_and_allows_native_fallback(self):
        definition, blob = grid_mesh(4)
        converted, data, stats = simplify_mesh(definition, blob, budget=1, max_work=1)
        self.assertFalse(stats['budget_met'])
        self.assertTrue(stats['contraction_work_limit_reached'])
        self.assertEqual(stats['collapses'], 0)
        self.assertEqual(stats['components_before'], stats['components_after'])
        positions, faces = _decode(converted, data)
        original_positions, original_faces = _decode(definition, blob)
        self.assertEqual(positions, original_positions)
        self.assertEqual(len(faces), len(original_faces))
        with tempfile.TemporaryDirectory() as temp:
            package = Path(temp)
            content = package / 'content'
            content.mkdir()
            msh = content / 'body.msh'
            msh.write_text(dumps(definition), encoding='utf-8')
            Path(str(msh) + '.blob').write_bytes(blob)
            model = {'lods': {1: {'node': {'name': 'RootNode', 'mesh': '/body.msh'}},
                              2: {'node': {'name': 'RootNode', 'mesh': '/body.msh'}}}, 'version': 2}
            (content / 'train.mdl').write_text(dumps(model), encoding='utf-8')
            with patch('converter.transforms.lod.MAX_CONTRACTION_WORK', 1), \
                 patch('converter.transforms.lod.TARGET_BYTES', 1200):
                diagnostics = optimize_lowest_lods(package)
            self.assertTrue(any(d['code'] == 'LOWEST_LOD_OPTIMIZED' for d in diagnostics), diagnostics)
            self.assertEqual(Path(str(msh) + '.blob').read_bytes(), blob)
            generated = list((content / '_assets/generated_lod').glob('*.msh'))
            self.assertTrue(generated)
            self.assertFalse(any(d['severity'] == 'error' for d in validate_mesh(
                parse(generated[0].read_text('utf-8')), Path(str(generated[0]) + '.blob'))))

    def test_planar_contraction_preserves_surface_uv_outline(self):
        definition,blob=grid_mesh(10)
        converted,data,stats=simplify_mesh(definition,blob,budget=1200)
        self.assertTrue(stats['budget_met'])
        self.assertGreater(stats['collapses'],0)
        self.assertLessEqual(stats['max_plane_error'],1e-8)
        positions,faces=_decode(converted,data)
        self.assertEqual(min(p[0] for p in positions),0)
        self.assertEqual(max(p[0] for p in positions),10)
        self.assertEqual(min(p[1] for p in positions),0)
        self.assertEqual(max(p[1] for p in positions),10)
        for face in faces:
            self.assertEqual(face['normal'],(0.,0.,1.))
            for vid,attrs in zip(face['ids'],face['attrs']):
                p=positions[vid]
                self.assertAlmostEqual(attrs['uv0'][0],p[0]/10,places=5)
                self.assertAlmostEqual(attrs['uv0'][1],p[1]/10,places=5)
                self.assertEqual(attrs['tangent'][3],-1.)

    def test_render_estimate_and_raw_budget_are_distinct(self):
        definition,blob=grid_mesh(1)
        stats=mesh_budget(definition,blob)
        self.assertEqual(stats['budget_bytes'],len(blob))
        self.assertGreater(stats['render_bytes'],len(blob))

    def test_cancel(self):
        definition,blob=grid_mesh(10)
        with self.assertRaises(InterruptedError):
            simplify_mesh(definition,blob,budget=1,cancel=lambda:True)

    def test_submesh_slots_preserved(self):
        definition,blob=grid_mesh(2)
        positions,faces=_decode(definition,blob)
        definition['subMeshes'][2]=deepcopy(definition['subMeshes'][1])
        for face in faces[4:]: face['sub']=1
        definition,blob,_=_pack(definition,positions,faces)
        converted,data,stats=simplify_mesh(definition,blob,budget=100000)
        _,converted_faces=_decode(converted,data)
        self.assertEqual(set(f['sub'] for f in converted_faces),{0,1})
        self.assertEqual(len(converted['subMeshes']),2)

    def test_reference_fatal_mesh_reaches_game_budget(self):
        root=Path(__file__).resolve().parents[1]
        path=root/'tpf2参考/9000000009/res/models/mesh/vehicle/train/JNR_M113_1112/Body_lod0.msh'
        if not path.exists(): self.skipTest('Reference fixture is not installed')
        blob_path=Path(str(path)+'.blob'); original=blob_path.read_bytes()
        before=hashlib.sha256(original).digest()
        definition=parse(path.read_text(encoding='utf-8-sig'))
        converted,data,stats=simplify_mesh(definition,original)
        self.assertTrue(stats['budget_met'])
        self.assertLessEqual(len(data),240*1024)
        self.assertLess(stats['max_plane_error'],.03)
        self.assertEqual(stats['components_before'],stats['components_after'])
        self.assertEqual(hashlib.sha256(blob_path.read_bytes()).digest(),before)
        with tempfile.TemporaryDirectory() as temp:
            output=Path(temp)/'far.msh.blob'; output.write_bytes(data)
            self.assertFalse(any(d['severity']=='error' for d in validate_mesh(converted,output)))

    def test_tiny_disconnected_component_cannot_disappear(self):
        definition,blob=grid_mesh(1)
        positions,faces=_decode(definition,blob)
        start=len(positions)
        positions.extend([(2.,0.,0.),(2.001,0.,0.),(2.,.0001,0.)])
        attrs=[]
        for p in positions[start:]:
            attrs.append({'position':p,'normal':(0.,0.,1.),'tangent':(1.,0.,0.,-1.),'uv0':(p[0],p[1])})
        faces.append({'ids':[start,start+1,start+2],'attrs':attrs,'sub':0,'normal':(0.,0.,1.)})
        definition,blob,_=_pack(definition,positions,faces)
        converted,data,stats=simplify_mesh(definition,blob,budget=1)
        self.assertEqual(stats['components_before'],2)
        self.assertEqual(stats['components_after'],2)
        positions,faces=_decode(converted,data)
        self.assertTrue(any(all(positions[v][0]>=2. for v in f['ids']) for f in faces))

    def test_failed_whole_lod_is_not_written(self):
        with tempfile.TemporaryDirectory() as temp:
            package=Path(temp); content=package/'content'; content.mkdir()
            definition,blob=grid_mesh(4)
            msh=content/'body.msh'; msh.write_text(dumps(definition),encoding='utf-8')
            Path(str(msh)+'.blob').write_bytes(blob)
            model={'lods':{1:{'node':{'name':'RootNode','mesh':'/body.msh'}},2:{'node':{'name':'RootNode','mesh':'/body.msh'}}},'version':2}
            mdl=content/'train.mdl'; original=dumps(model); mdl.write_text(original,encoding='utf-8')
            with patch('converter.transforms.lod.TARGET_BYTES',1):
                diagnostics=optimize_lowest_lods(package)
            self.assertTrue(any(d['code']=='LOWEST_LOD_SIMPLIFICATION_LIMIT' for d in diagnostics))
            self.assertEqual(mdl.read_text(encoding='utf-8'),original)
            self.assertEqual(Path(str(msh)+'.blob').read_bytes(),blob)
            self.assertFalse((content/'_assets/generated_lod').exists())

    def test_motion_branches_are_all_protected(self):
        with tempfile.TemporaryDirectory() as temp:
            package=Path(temp); content=package/'content'; content.mkdir()
            definition,blob=grid_mesh(2)
            msh=content/'body.msh'; msh.write_text(dumps(definition),encoding='utf-8'); Path(str(msh)+'.blob').write_bytes(blob)
            children={1:{'name':'door1','mesh':'/body.msh','animations':{'open':{}}},
                      2:{'name':'door2','mesh':'/body.msh','animations':{'open':{}}},
                      3:{'name':'bogie','children':{1:{'name':'axle','mesh':'/body.msh'},2:{'name':'frame','mesh':'/body.msh'}}}}
            lod={'node':{'name':'RootNode','children':children}}
            model={'lods':{1:deepcopy(lod),2:lod},'metadata':{'railVehicle':{'config':{'axles':{1:'axle'}}}},'version':2}
            mdl=content/'train.mdl'; original=dumps(model); mdl.write_text(original,encoding='utf-8')
            with patch('converter.transforms.lod.TARGET_BYTES',1): diagnostics=optimize_lowest_lods(package)
            self.assertTrue(any(d['code']=='LOWEST_LOD_MOVABLE' for d in diagnostics))
            self.assertEqual(mdl.read_text(encoding='utf-8'),original)


class ParallelLodTests(unittest.TestCase):
    """The farthest-LOD simplification runs on several processes; the result must not depend on the worker count."""

    @staticmethod
    def build(root):
        content = Path(root) / 'content'
        content.mkdir(parents=True)
        for index, side in enumerate((62, 70), 1):
            definition, blob = grid_mesh(side)  # both exceed the 240 KiB target
            msh = content / f'body{index}.msh'
            msh.write_text(dumps(definition), encoding='utf-8')
            Path(str(msh) + '.blob').write_bytes(blob)
        for name, mesh in (('a', 1), ('b', 2), ('c', 1)):  # c shares its mesh with a: one job for both
            lod = {'node': {'name': 'RootNode', 'mesh': f'/body{mesh}.msh'}}
            (content / f'{name}.mdl').write_text(dumps({'lods': {1: lod, 2: deepcopy(lod)}, 'version': 2}), encoding='utf-8')
        return Path(root)

    def test_workers_do_not_change_the_output(self):
        outputs, codes = {}, {}
        with tempfile.TemporaryDirectory() as temp:
            for workers in (1, 2):
                package = self.build(Path(temp) / f'w{workers}')
                diagnostics = optimize_lowest_lods(package, workers=workers)
                codes[workers] = [d['code'] for d in diagnostics]
                content = package / 'content'
                outputs[workers] = {p.relative_to(content).as_posix(): p.read_bytes() for p in sorted(content.rglob('*')) if p.is_file()}
        self.assertEqual(codes[1], ['LOWEST_LOD_OPTIMIZED'] * 3)
        self.assertEqual(codes[1], codes[2])
        self.assertEqual(outputs[1], outputs[2])
        self.assertTrue(any(name.startswith('_assets/generated_lod/') for name in outputs[2]))

    def test_cancel_stops_the_parallel_run(self):
        import threading
        stop = threading.Event()
        stop.set()
        with tempfile.TemporaryDirectory() as temp:
            package = self.build(Path(temp) / 'cancelled')
            with self.assertRaises(InterruptedError):
                optimize_lowest_lods(package, cancel=stop, workers=2)


if __name__=='__main__': unittest.main()
