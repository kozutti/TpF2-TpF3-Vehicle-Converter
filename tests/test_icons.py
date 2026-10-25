from pathlib import Path
import struct
import tempfile
import unittest

from converter.pipeline import convert, validate
from converter.transforms.icons import icon_mapping, read_tga, write_tga
from test_conversion import fixture


def tga(width, height, bpp=32, kind=10, descriptor=8, payload=b''):
    return bytes([0, 0, kind]) + bytes(9) + struct.pack('<HH', width, height) + bytes([bpp, descriptor]) + payload


def palette_tga():
    # Bottom row first: two palette entries with distinct alpha values.
    header = bytes([0, 1, 1]) + struct.pack('<HHB', 0, 2, 32) + bytes(4)
    header += struct.pack('<HH', 2, 2) + bytes([8, 8])
    return header + bytes([0, 0, 255, 255, 255, 0, 0, 128]) + bytes([1, 0, 0, 1])


class IconTests(unittest.TestCase):
    def test_palette_icons_preserve_colour_alpha_and_origin(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / 'palette.tga'
            path.write_bytes(palette_tga())
            red, blue = bytes([0, 0, 255, 255]), bytes([255, 0, 0, 128])
            self.assertEqual(read_tga(path), (2, 2, 40, red + blue + blue + red))
            path.write_bytes(palette_tga()[:-1])
            self.assertIsNone(read_tga(path))

    def test_palette_purchase_icons_are_normalized_and_source_is_unchanged(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / 'mod'
            fixture(root)
            for folder in ('models_20', 'models_small'):
                path = root / f'res/textures/ui/{folder}/vehicle/train/unit@2x.tga'
                path.parent.mkdir(parents=True)
                path.write_bytes(palette_tga())
            result = convert(root, Path(td) / 'out', texture_workers=60)
            self.assertEqual(result['status'], 'success', result['diagnostics'])
            icons = Path(result['package_dir']) / 'content/vehicle/train/icons'
            for suffix in ('icon20@2x', 'icon_small@2x', 'store'):
                output = icons / f'unit_{suffix}.tga'
                self.assertEqual(output.read_bytes()[1:3], b'\0\x02')
                self.assertTrue(any(read_tga(output)[3][3::4]))
            for folder in ('models_20', 'models_small'):
                self.assertEqual((root / f'res/textures/ui/{folder}/vehicle/train/unit@2x.tga').read_bytes(), palette_tga())
            self.assertTrue(validate(result['package_dir'])['valid'])

    def test_rle_raw_and_run_packets_and_24bit_alpha(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / 'image.tga'
            red = bytes([0, 0, 255, 255]); blue = bytes([255, 0, 0, 128])
            path.write_bytes(tga(2, 2, payload=bytes([129]) + red + bytes([1]) + blue + red))
            self.assertEqual(read_tga(path), (2, 2, 8, red * 2 + blue + red))
            path.write_bytes(tga(2, 1, bpp=24, descriptor=32, payload=bytes([129]) + red[:3]))
            self.assertEqual(read_tga(path), (2, 1, 40, red * 2))
            path.write_bytes(tga(2, 1, bpp=24, kind=2, payload=red[:3] + blue[:3]))
            self.assertEqual(read_tga(path)[3], red + blue[:3] + b'\xff')

    def test_malformed_rle_and_excessive_dimensions_are_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / 'image.tga'
            for raw in (tga(1, 1, payload=b'\x81\0\0\0\xff'),  # packet overruns image
                        tga(2, 1, payload=b'\x01\0\0\0\xff'),  # raw packet truncated
                        tga(1, 1, payload=b'\x80\0\0'), tga(0, 1), tga(65535, 65535)):
                path.write_bytes(raw)
                self.assertIsNone(read_tga(path))

    def test_plain_group_icon_is_mapped_and_2x_wins_without_collision(self):
        model = 'res/models/model/vehicle/train/group.mdl'
        plain = 'res/textures/ui/models_small/vehicle/train/group.tga'
        double = 'res/textures/ui/models_small/vehicle/train/group@2x.tga'
        mapping = {model: 'vehicle/train/group.mdl'}
        self.assertEqual(icon_mapping([model, plain], mapping, []), {plain: 'vehicle/train/icons/group_icon_small@2x.tga'})
        for files in ([model, plain, double], [model, double, plain]):
            diagnostics = []
            self.assertEqual(icon_mapping(files, mapping, diagnostics), {double: 'vehicle/train/icons/group_icon_small@2x.tga'})
            self.assertEqual([d['code'] for d in diagnostics], ['ICON_DUPLICATE_SKIPPED'])

    def test_unscaled_rle_icon_is_converted_with_visible_store_pixels(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / 'mod'
            fixture(root)
            path = root / 'res/textures/ui/models_small/vehicle/train/unit.tga'
            path.parent.mkdir(parents=True)
            original = tga(4, 2, payload=b'\x87' + bytes([40, 80, 160, 255]))
            path.write_bytes(original)
            result = convert(root, Path(td) / 'out', texture_workers=60)
            self.assertEqual(result['status'], 'success', result['diagnostics'])
            icons = Path(result['package_dir']) / 'content/vehicle/train/icons'
            small = read_tga(icons / 'unit_icon_small@2x.tga')
            self.assertEqual(small[3], bytes([40, 80, 160, 255]) * 8)
            self.assertEqual((icons / 'unit_icon_small@2x.tga').read_bytes()[2], 2)
            store = read_tga(icons / 'unit_store.tga')
            self.assertIsNotNone(store)
            self.assertTrue(any(store[3][3::4]))
            self.assertEqual(path.read_bytes(), original)
            self.assertTrue(validate(result['package_dir'])['valid'])

    def test_cblend_is_preserved_when_it_is_the_model_name(self):
        model = 'res/models/model/vehicle/train/MKN_E233_cblend.mdl'
        color = 'res/textures/ui/models_small/vehicle/train/MKN_E233_cblend@2x.tga'
        mask = 'res/textures/ui/models_small/vehicle/train/MKN_E233_cblend_cblend@2x.tga'
        diagnostics = []
        mapping = {model: 'vehicle/train/mkn_e233_cblend.mdl'}
        self.assertEqual(icon_mapping([model, color, mask], mapping, diagnostics), {
            color: 'vehicle/train/icons/mkn_e233_cblend_icon_small@2x.tga',
            mask: 'vehicle/train/icons/mkn_e233_cblend_icon_small_cblend@2x.tga'})
        self.assertEqual(diagnostics, [])
        ordinary = 'res/models/model/vehicle/train/unit.mdl'
        ordinary_mask = 'res/textures/ui/models_small/vehicle/train/unit_cblend@2x.tga'
        self.assertEqual(icon_mapping([ordinary, ordinary_mask], {ordinary: 'vehicle/train/unit.mdl'}, []),
                         {ordinary_mask: 'vehicle/train/icons/unit_icon_small_cblend@2x.tga'})


if __name__ == '__main__':
    unittest.main()
