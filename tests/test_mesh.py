from pathlib import Path
import struct
import tempfile
import unittest

from converter.codecs.mesh import validate_mesh
from converter.parsers.lua import parse


class MeshTests(unittest.TestCase):
    def mesh(self, indices=(0, 1, 2), floating=(0., 0., 0., 1., 0., 0., 0., 1., 0.)):
        definition = {'vertexAttr': {'position': {'offset': 0, 'count': 36, 'numComp': 3}},
                      'subMeshes': {1: {'indices': {'position': {'offset': 36, 'count': 12}}}}}
        blob = struct.pack('<9f3I', *floating, *indices)
        return definition, blob

    def check(self, definition, blob):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'test.msh.blob'
            path.write_bytes(blob)
            return validate_mesh(definition, path)

    def test_valid_bytes_profile(self):
        definition, blob = self.mesh()
        self.assertEqual(self.check(definition, blob), [])

    def test_invalid_index(self):
        definition, blob = self.mesh((0, 1, 3))
        self.assertIn('MESH_INDEX_BOUNDS', {d['code'] for d in self.check(definition, blob)})

    def test_truncated(self):
        definition, blob = self.mesh()
        self.assertIn('MESH_BLOB_RANGE', {d['code'] for d in self.check(definition, blob[:-4])})

    def test_nonfinite(self):
        definition, blob = self.mesh(floating=(float('nan'),) + (0.,) * 8)
        self.assertIn('MESH_NONFINITE', {d['code'] for d in self.check(definition, blob)})

    def test_invalid_component_count(self):
        definition, blob = self.mesh()
        definition['vertexAttr']['position']['numComp'] = 0
        self.assertIn('MESH_COMPONENTS', {d['code'] for d in self.check(definition, blob)})

    def test_tangent_nonfinite_details(self):
        definition, blob = self.mesh()
        definition['vertexAttr']['tangent'] = {'offset': 48, 'count': 48, 'numComp': 4}
        blob += struct.pack('<12f', float('nan'), float('nan'), float('nan'), 1.,
                            0., 1., 0., -1., 0., 0., 1., 1.)
        diagnostics = self.check(definition, blob)
        self.assertEqual(len(diagnostics), 1)
        self.assertEqual(diagnostics[0]['severity'], 'warning')
        self.assertEqual(diagnostics[0]['component_nonfinite_counts'], [1,1,1,0])
        self.assertEqual(diagnostics[0]['affected_item_count'], 1)
        self.assertEqual(diagnostics[0]['total_item_count'], 3)

    def test_reference_blobs(self):
        root = Path(__file__).resolve().parents[1]
        for dirname in ('tpf2参考', 'tpf3参考'):
            files = sorted((root / dirname).rglob('*.msh'))[:2]
            for path in files:
                with self.subTest(path=str(path)):
                    definition = parse(path.read_text(encoding='utf-8-sig'), str(path))
                    self.assertEqual(validate_mesh(definition, str(path) + '.blob'), [])


if __name__ == '__main__':
    unittest.main()
