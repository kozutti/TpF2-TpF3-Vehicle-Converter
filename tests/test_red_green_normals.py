"""Declared XY normal fallback: gray unused B must not become tangent Z."""
from pathlib import Path
import tempfile
import struct
import unittest

from converter.parsers.lua import dumps, parse
from converter.transforms.textures import convert_dds, inspect_dds, _decode_bc4
from converter.transforms.texture_usage import convert_package_textures
from test_textures import dds


def gray_bc2():
    # (123,125,123) is not a usable RGB unit normal, but XY are near-flat.
    endpoint = (15 << 11) | (31 << 5) | 15
    block = b'\xff' * 8 + struct.pack('<HHI', endpoint, endpoint, 0)
    return dds(b'DXT3', [block] * 3, mip_count=3)


class DeclaredRGNormalTests(unittest.TestCase):
    def test_declared_xy_converts_gray_bc2_without_normalizing_by_unused_blue(self):
        with tempfile.TemporaryDirectory() as td:
            source = Path(td) / 'normal.dds'
            source.write_bytes(gray_bc2())
            original = source.read_bytes()
            undeclared = Path(td) / 'rgb.dds'
            ordinary = convert_dds(source, undeclared, 'normal')
            self.assertEqual(ordinary['format_after'], 'BC2')
            self.assertEqual(undeclared.read_bytes(), original)
            target = Path(td) / 'rg.dds'
            result = convert_dds(source, target, 'normal', normal_layout='rgb_with_rg_fallback')
            self.assertEqual(result['format_after'], 'BC5', result)
            self.assertIn('TEXTURE_DECLARED_RG_NORMAL', [d['code'] for d in result['diagnostics']])
            info = inspect_dds(target)
            self.assertEqual(info['mip_count'], 3)
            raw = target.read_bytes()
            for mip in info['mips']:
                offset = mip['offset']
                self.assertEqual(set(_decode_bc4(raw[offset:offset + 8])), {123})
                self.assertEqual(set(_decode_bc4(raw[offset + 8:offset + 16])), {125})
            self.assertEqual(source.read_bytes(), original)

    def test_valid_rgb_and_packed_alpha_keep_the_existing_policy(self):
        with tempfile.TemporaryDirectory() as td:
            source = Path(td) / 'normal.dds'
            color = struct.pack('<HHI', 0x841f, 0x841f, 0)
            source.write_bytes(dds(b'DXT1', [color]))
            outputs = []
            for layout in ('rgb', 'rgb_with_rg_fallback'):
                target = Path(td) / (layout + '.dds')
                result = convert_dds(source, target, 'normal', normal_layout=layout)
                self.assertEqual(result['format_after'], 'BC5')
                self.assertNotIn('TEXTURE_DECLARED_RG_NORMAL', [d['code'] for d in result['diagnostics']])
                outputs.append(target.read_bytes())
            self.assertEqual(outputs[0], outputs[1])
            packed = dds(b'DXT5', [bytes((64, 128)) + b'\xaa' * 6 + color])
            source.write_bytes(packed)
            result = convert_dds(source, Path(td) / 'packed.dds', 'normal', normal_layout='rgb_with_rg_fallback')
            self.assertEqual(result['format_after'], 'BC3')
            self.assertEqual((Path(td) / 'packed.dds').read_bytes(), packed)

    def test_shared_source_has_independent_declared_xy_and_rgb_variants(self):
        with tempfile.TemporaryDirectory() as td:
            package = Path(td)
            content = package / 'content'
            content.mkdir()
            source = content / 'shared.dds'
            source.write_bytes(gray_bc2())
            original = source.read_bytes()
            for name, flag in (('rgb', False), ('rg', True), ('rg_shared', True)):
                data = {'type': 'PHYSICAL_NRML_MAP', 'params': {'map_normal': {
                    'fragmentSamplers': {'normalTex': {'fileName': '/shared.dds', 'redGreen': flag}}}}}
                (content / (name + '.mtl')).write_text(dumps(data), 'utf-8')
            diagnostics = convert_package_textures(package, workers=2)
            references = {name: parse((content / (name + '.mtl')).read_text('utf-8'))['params']['map_normal']
                          ['fragmentSamplers']['normalTex']['fileName'] for name in ('rgb', 'rg', 'rg_shared')}
            self.assertNotEqual(references['rgb'], references['rg'])
            self.assertEqual(references['rg'], references['rg_shared'])
            self.assertEqual(inspect_dds(content / references['rgb'].lstrip('/'))['format'], 'BC2')
            self.assertEqual(inspect_dds(content / references['rg'].lstrip('/'))['format'], 'BC5')
            self.assertEqual(sum(d['code'] == 'TEXTURE_DECLARED_RG_NORMAL' for d in diagnostics), 1)
            self.assertEqual(source.read_bytes(), original)


if __name__ == '__main__':
    unittest.main()
