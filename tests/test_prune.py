from pathlib import Path
import tempfile
import unittest

from converter.parsers.lua import dumps, parse
from converter.pipeline import convert, validate
from converter.transforms.prune import prune_unused_textures

from test_conversion import fixture
from test_texture_usage import dxt1


class PruneTests(unittest.TestCase):
    def test_originals_replaced_by_converted_textures_are_not_shipped(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / 'mod'
            fixture(root)
            texture = root / 'res/textures/vehicle/unit_nomal.dds'
            texture.parent.mkdir(parents=True, exist_ok=True)
            texture.write_bytes(dxt1())
            material = root / 'res/models/material/vehicle/train/unit/plain.mtl'
            data = parse(material.read_text('utf-8'))
            data['params']['map_normal'] = {'fileName': 'vehicle/unit_nomal.dds', 'type': 'TWOD'}
            material.write_text(dumps(data), 'utf-8')
            result = convert(root, Path(td) / 'out', siblings=None)
            self.assertEqual(result['status'], 'success', [d for d in result['diagnostics'] if d['severity'] == 'error'])
            package = Path(result['package_dir'])
            self.assertTrue(any(package.rglob('converted_textures/*.dds')))
            self.assertEqual([p.name for p in (package / 'content/_assets/texture').rglob('*') if p.is_file()], [])
            self.assertIn('TEXTURES_PRUNED', {d['code'] for d in result['diagnostics']})
            self.assertTrue(validate(package)['valid'])
            self.assertTrue(texture.is_file())  # the source MOD is untouched

    def test_referenced_textures_stay_and_relative_references_stop_the_pruning(self):
        with tempfile.TemporaryDirectory() as td:
            package = Path(td)
            used = package / 'content/_assets/texture/a/used.dds'
            unused = package / 'content/_assets/texture/a/unused.dds'
            used.parent.mkdir(parents=True)
            used.write_bytes(b'x' * 10)
            unused.write_bytes(b'y' * 10)
            material = package / 'content/_assets/material/m.mtl'
            material.parent.mkdir(parents=True)
            material.write_text('function data() return { fileName = "/_assets/texture/a/used.dds" } end', 'utf-8')
            self.assertEqual([d['code'] for d in prune_unused_textures(package)], ['TEXTURES_PRUNED'])
            self.assertTrue(used.is_file())
            self.assertFalse(unused.exists())
            unused.write_bytes(b'z')
            material.write_text('function data() return { fileName = "a/used.dds" } end', 'utf-8')
            self.assertEqual(prune_unused_textures(package), [])
            self.assertTrue(unused.is_file())


if __name__ == '__main__':
    unittest.main()
