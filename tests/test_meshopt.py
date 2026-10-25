import tempfile
from pathlib import Path
import unittest
from converter.codecs.mesh import validate_mesh
from converter.transforms.lod import _decode
from converter.transforms.meshopt import simplify_meshopt, components
from tests.test_lod import grid_mesh

class MeshoptTests(unittest.TestCase):
    def test_wasm_reduces_generic_mesh_preserving_extents_and_uv(self):
        definition,blob=grid_mesh(12)
        converted,data,stats=simplify_meshopt(definition,blob,budget=4000)
        self.assertTrue(stats['budget_met'])
        positions,faces=_decode(converted,data)
        self.assertEqual(len(components(faces)),stats['components_before'])
        self.assertEqual((min(p[0] for p in positions),max(p[0] for p in positions)),(0,12))
        self.assertEqual((min(p[1] for p in positions),max(p[1] for p in positions)),(0,12))
        for face in faces:
            self.assertGreater(face['normal'][2],0)
            for vid,attrs in zip(face['ids'],face['attrs']):
                self.assertAlmostEqual(attrs['uv0'][0],positions[vid][0]/12,places=4)
                self.assertAlmostEqual(attrs['uv0'][1],positions[vid][1]/12,places=4)
                self.assertEqual(attrs['tangent'][3],-1.)
        with tempfile.TemporaryDirectory() as td:
            output=Path(td)/'far.msh.blob'; output.write_bytes(data)
            self.assertFalse(any(d['severity']=='error' for d in validate_mesh(converted,output)))
