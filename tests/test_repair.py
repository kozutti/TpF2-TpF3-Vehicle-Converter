from pathlib import Path
import tempfile
import unittest

from converter.parsers.lua import parse
from converter.pipeline import convert
from converter.resolver import array, walk
from converter.transforms.repair import drop_broken_meshes

from test_conversion import fixture


class BrokenMeshTests(unittest.TestCase):
    def test_a_damaged_mesh_is_deleted_and_its_nodes_are_left_out_of_the_models(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / 'mod'
            fixture(root)
            result = convert(root, Path(td) / 'out', siblings=None)
            package = Path(result['package_dir'])
            content = package / 'content'
            meshes = sorted(content.rglob('*.msh'))
            self.assertTrue(meshes)
            blob = Path(str(meshes[0]) + '.blob')
            blob.write_bytes(blob.read_bytes()[:8])  # the vertex ranges now lie outside the file
            models = sorted((content / 'vehicle').rglob('*.mdl'))
            before = sum(1 for model in models for key, value, at in walk(parse(model.read_text('utf-8'))) if key == 'mesh')
            diagnostics = drop_broken_meshes(package)
            self.assertEqual([(d['code'], d['severity']) for d in diagnostics], [('MESH_BROKEN_DROPPED', 'warning')])
            self.assertFalse(meshes[0].exists() or blob.exists())
            after = sum(1 for model in models for key, value, at in walk(parse(model.read_text('utf-8'))) if key == 'mesh')
            self.assertLess(after, before)

    def test_healthy_packages_are_left_alone(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / 'mod'
            fixture(root)
            result = convert(root, Path(td) / 'out', siblings=None)
            self.assertEqual(drop_broken_meshes(result['package_dir']), [])


if __name__ == '__main__':
    unittest.main()
