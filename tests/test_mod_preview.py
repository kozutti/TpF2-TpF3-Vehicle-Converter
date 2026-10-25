from pathlib import Path
import tempfile
import unittest

from PIL import Image

from converter.pipeline import convert
from converter.transforms.mod_preview import PREVIEW_SIZE, write_mod_previews
from test_conversion import fixture


class ModPreviewTests(unittest.TestCase):
    def test_conversion_preserves_source_and_writes_browser_and_detail_images(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / 'mod'
            fixture(root)
            # Portrait picture must remain a portrait, without cropping.
            Image.new('RGB', (24, 48), (200, 20, 10)).save(root / 'image_00.tga', compression='tga_rle')
            Image.new('RGB', (96, 24), (10, 40, 220)).save(root / 'image_02.png')
            before = {p.name: p.read_bytes() for p in root.glob('image_*')}
            result = convert(root, Path(td) / 'out', texture_workers=60)
            self.assertEqual(result['status'], 'success', result['diagnostics'])
            metadata = Path(result['package_dir']) / '_metadata'
            with Image.open(metadata / '0.png') as image:
                self.assertEqual(image.size, PREVIEW_SIZE)
                self.assertEqual(image.getpixel((960, 540)), (200, 20, 10))
                self.assertEqual(image.getpixel((0, 540)), (30, 35, 40))
                self.assertEqual(image.getpixel((960, 0)), (200, 20, 10))
            with Image.open(metadata / '1.png') as image:
                self.assertEqual(image.getpixel((960, 540)), (10, 40, 220))
            self.assertEqual(before, {p.name: p.read_bytes() for p in root.glob('image_*')})

    def test_corrupt_first_picture_does_not_leave_grid_missing(self):
        with tempfile.TemporaryDirectory() as td:
            root, package = Path(td) / 'mod', Path(td) / 'package'
            root.mkdir()
            (root / 'image_00.tga').write_bytes(b'broken')
            Image.new('RGB', (16, 9), 'green').save(root / 'image_01.jpg')
            diagnostics = write_mod_previews(root, package)
            self.assertTrue((package / '_metadata/0.png').is_file())
            self.assertEqual([d['code'] for d in diagnostics], ['MOD_PREVIEW_INVALID', 'MOD_PREVIEW_CONVERTED'])

    def test_store_picture_is_used_when_workshop_preview_is_absent(self):
        with tempfile.TemporaryDirectory() as td:
            root, package = Path(td) / 'mod', Path(td) / 'package'
            root.mkdir()
            icon = package / 'content/vehicle/train/icons/unit_store.tga'
            icon.parent.mkdir(parents=True)
            Image.new('RGBA', (453, 224), (255, 0, 0, 255)).save(icon)
            diagnostics = write_mod_previews(root, package)
            self.assertEqual([d['code'] for d in diagnostics], ['MOD_PREVIEW_CONVERTED'])
            self.assertTrue((package / '_metadata/0.png').is_file())


if __name__ == '__main__':
    unittest.main()
