from pathlib import Path
import tempfile
import unittest
import zipfile

from converter.adapters.game_assets import game_input, SiblingIndex
from converter.parsers.lua import dumps
from converter.pipeline import convert
from converter.transforms.icons import read_tga, write_tga
from tests.test_conversion import fixture


class DependencyIconTests(unittest.TestCase):
    def test_local_repaint_model_imports_its_exact_colour_images_from_base(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            root, provider = base / 'repaint', base / 'provider'
            fixture(root)
            pixel = bytes([100, 20, 180, 255])
            for folder in ('models_20', 'models_small'):
                icons = provider / f'res/textures/ui/{folder}/vehicle/train'
                icons.mkdir(parents=True)
                write_tga(icons / 'unit@2x.tga', (2, 1), 40, pixel * 2)
                other = provider / f'res/textures/ui/{folder}/vehicle/other'
                other.mkdir(parents=True)
                write_tga(other / 'unit@2x.tga', (2, 1), 40, bytes([0, 0, 0, 255]) * 2)
            local_mask = root / 'res/textures/ui/models_small/vehicle/train/unit_cblend@2x.tga'
            local_mask.parent.mkdir(parents=True)
            write_tga(local_mask, (2, 1), 40, bytes([0, 255, 0, 255]) * 2)
            before = {p: p.read_bytes() for folder in (root, provider)
                      for p in folder.rglob('*') if p.is_file()}
            result = convert(root, base / 'out', siblings=SiblingIndex(base), texture_workers=60)
            self.assertEqual(result['status'], 'success', result['diagnostics'])
            content = Path(result['package_dir']) / 'content'
            icons = content / 'vehicle/train/icons'
            self.assertEqual(read_tga(icons / 'unit_icon_small@2x.tga')[3], pixel * 2)
            self.assertEqual(read_tga(icons / 'unit_icon20@2x.tga')[3], pixel * 2)
            self.assertEqual(read_tga(icons / 'unit_icon_small_cblend@2x.tga')[3], read_tga(local_mask)[3])
            self.assertTrue(any(read_tga(icons / 'unit_store.tga')[3][3::4]))
            self.assertEqual(len(list(content.rglob('*.mdl'))), 1)
            self.assertEqual(before, {p: p.read_bytes() for p in before})

    def test_imported_car_images_reach_purchase_output_without_source_changes(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            root, provider = base / 'addon', base / 'provider'
            fixture(provider)
            root.mkdir()
            (root / 'mod.lua').write_text(dumps({'info': {'name': 'addon'}}), 'utf-8')
            unit = root / 'res/config/multiple_unit/unit.lua'
            unit.parent.mkdir(parents=True)
            unit.write_text(dumps({'name': 'unit', 'vehicles': {1: {
                'name': 'vehicle/train/unit.mdl', 'forward': True}}}), 'utf-8')
            pixel = bytes([30, 80, 180, 255])
            for folder in ('models_20', 'models_small'):
                icons = provider / f'res/textures/ui/{folder}/vehicle/train'
                icons.mkdir(parents=True)
                for suffix in ('@2x.tga', '_cblend@2x.tga', '.tga'):
                    write_tga(icons / ('unit' + suffix), (2, 1), 40, pixel * 2)
                write_tga(icons / 'unrelated@2x.tga', (2, 1), 40, pixel * 2)
            before = {p: p.read_bytes() for folder in (root, provider)
                      for p in folder.rglob('*') if p.is_file()}
            result = convert(root, base / 'out', siblings=SiblingIndex(base), texture_workers=60)
            self.assertEqual(result['status'], 'success', result['diagnostics'])
            icons = Path(result['package_dir']) / 'content/vehicle/train/icons'
            for suffix in ('_icon20@2x.tga', '_icon20_cblend@2x.tga',
                           '_icon_small@2x.tga', '_icon_small_cblend@2x.tga', '_store.tga'):
                self.assertTrue(any(read_tga(icons / ('unit' + suffix))[3][3::4]))
            self.assertFalse(list(icons.glob('unrelated*')))
            self.assertEqual(before, {p: p.read_bytes() for p in before})

    def test_archived_group_images_and_original_override(self):
        with tempfile.TemporaryDirectory() as td:
            root, game = Path(td) / 'addon', Path(td) / 'game'
            root.mkdir(); (game / 'res').mkdir(parents=True)
            (root / 'mod.lua').write_text('function data() return {model="vehicle/train/group.mdl"} end', 'utf-8')
            image = 'res/textures/ui/models_small/vehicle/train/group.tga'
            override = root / image
            override.parent.mkdir(parents=True); override.write_bytes(b'original')
            with zipfile.ZipFile(game / 'res/base.zip', 'w') as archive:
                archive.writestr('models/model/vehicle/train/group.mdl', dumps({}))
                archive.writestr(image.removeprefix('res/'), b'provider')
                archive.writestr('textures/ui/models_20/vehicle/train/group@2x.tga', b'group icon')
                archive.writestr('textures/ui/models_small/vehicle/other/group.tga', b'wrong folder')
            with game_input(root, game) as (overlay, info):
                self.assertEqual((overlay / image).read_bytes(), b'original')
                self.assertEqual((overlay / 'res/textures/ui/models_20/vehicle/train/group@2x.tga').read_bytes(), b'group icon')
                self.assertNotIn(image, info['game_dependencies'])
                self.assertFalse((overlay / 'res/textures/ui/models_small/vehicle/other/group.tga').exists())
