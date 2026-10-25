from copy import deepcopy
from pathlib import Path
import tempfile
import unittest

from converter.adapters.tpf3_writer import convert_model
from converter.parsers.lua import dumps
from converter.resolver import table
from converter.transforms.icons import copy_asset_variant_icons, make_store_icons, read_tga, write_tga
from converter.transforms.singles import apply_single_vehicle_mode
from test_singles import tpf2_car, write_model, read_model, IDENTITY


class AssetVariantIconTests(unittest.TestCase):
    def package(self, root):
        content = root / 'content'
        mesh = content / '_assets/body.msh'
        mesh.parent.mkdir(parents=True)
        mesh.write_text('mesh', 'utf-8')
        texture = content / '_assets/body.dds'
        texture.write_bytes(b'texture')
        mat = {'params': {'map_albedo': {'fragmentSamplers': {'albedoTex': {'fileName': '/_assets/body.dds'}}}}}
        for name in ('train', 'asset'):
            (content / f'_assets/{name}.mtl').write_text(dumps(mat), 'utf-8')
        for model_name in ('a', 'b'):
            for folder in ('asset', 'vehicle/train'):
                model = convert_model(tpf2_car(['ELECTRIC']), 'car.mdl', [])
                model['lods'][1]['node']['children'] = table([{
                    'name': 'body', 'mesh': '/_assets/body.msh', 'transf': IDENTITY,
                    'materials': table(['/_assets/' + ('asset' if folder == 'asset' else 'train') + '.mtl'])}])
                write_model(content, f'{folder}/{model_name}.mdl', model)
                if folder == 'vehicle/train':
                    icons = content / folder / 'icons'
                    icons.mkdir(exist_ok=True)
                    for suffix in ('_icon_small@2x.tga', '_icon20@2x.tga', '_icon_small_cblend@2x.tga'):
                        write_tga(icons / (model_name + suffix), (1, 1), 40, b'\x11\x22\x33\xff')
        return content

    def test_matching_variant_and_its_generated_group_get_visible_images(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            content = self.package(root)
            before = {p: p.read_bytes() for p in (content/'vehicle').rglob('*') if p.is_file()}
            diagnostics = make_store_icons(root)
            self.assertEqual(sum(d['code']=='ASSET_VARIANT_ICONS_COPIED' for d in diagnostics), 2)
            self.assertEqual(before, {p: p.read_bytes() for p in before})
            apply_single_vehicle_mode(root, 'group')
            # one group for the whole MOD; its models share no folder, so the group sits in `vehicle`
            for icons, stems in ((content / 'asset/icons', ('a', 'b')), (content / 'vehicle/icons', ('single_group_rail',))):
                for stem in stems:
                    for suffix in ('_icon_small@2x.tga', '_store.tga'):
                        image = read_tga(icons / (stem + suffix))
                        self.assertIsNotNone(image)
                        self.assertTrue(any(image[3][3::4]))

    def test_same_name_with_different_body_or_albedo_never_borrows_icons(self):
        for change in ('body', 'root', 'albedo'):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as td:
                root = Path(td)
                content = self.package(root)
                if change in ('body', 'root'):
                    model = read_model(content, 'asset/a.mdl')
                    node = model['lods'][1]['node'] if change == 'root' else model['lods'][1]['node']['children'][1]
                    node['transf'] = deepcopy(IDENTITY)
                    node['transf'][13] = 10
                    write_model(content, 'asset/a.mdl', model)
                else:
                    (content/'_assets/other.dds').write_bytes(b'other')
                    (content/'_assets/asset.mtl').write_text(dumps({'params': {'map_albedo': {'fileName':'/_assets/other.dds'}}}), 'utf-8')
                copy_asset_variant_icons(root)
                self.assertFalse((content/'asset/icons/a_icon_small@2x.tga').exists())

    def test_explicit_asset_image_is_kept(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            content = self.package(root)
            explicit = content/'asset/icons/a_icon_small@2x.tga'
            explicit.parent.mkdir(parents=True)
            write_tga(explicit, (1,1), 40, b'\xff\0\0\xff')
            before = explicit.read_bytes()
            copy_asset_variant_icons(root)
            self.assertEqual(explicit.read_bytes(), before)
