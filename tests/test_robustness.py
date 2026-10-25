"""Inputs that used to stop a conversion (or make it partial) although TPF3 itself accepts the result."""
from pathlib import Path
import struct
import tempfile
import unittest

from converter.adapters.tpf3_writer import convert_model
from converter.parsers.lua import dumps, parse
from converter.pipeline import convert, validate
from converter.resolver import array, placeholder_texture, rewrite_refs, table
from converter.transforms.lod import _decode
from converter.transforms.textures import convert_dds

from test_conversion import fixture
from test_singles import tpf2_car


def bc7_dds(path, width=4, height=4):
    """A 4x4 BC7 texture (DX10 header, one block); the block content does not matter here."""
    header = bytearray(128)
    header[0:4] = b'DDS '
    struct.pack_into('<7I', header, 4, 124, 0x1 | 0x2 | 0x4 | 0x1000 | 0x20000, height, width, 16, 0, 1)
    struct.pack_into('<I', header, 76, 32)
    struct.pack_into('<I', header, 80, 4)
    header[84:88] = b'DX10'
    struct.pack_into('<I', header, 108, 0x1000)
    extension = struct.pack('<5I', 98, 3, 0, 1, 0)
    Path(path).write_bytes(bytes(header) + extension + bytes(16))


class EmissionTests(unittest.TestCase):
    def convert(self, emission):
        model = tpf2_car(['ELECTRIC'])
        model['metadata']['emission'] = emission
        diagnostics = []
        return convert_model(model, 'car.mdl', diagnostics), diagnostics

    def test_legacy_values_are_copied_to_noise_and_pollution_like_the_game_does(self):
        result, diagnostics = self.convert({'idleEmission': 50, 'powerEmission': 0, 'speedEmission': 0.8})
        expected = {'idle': 50, 'speed': 0.8, 'power': 0}
        self.assertEqual(result['metadata']['emissions'], {'noise': expected, 'pollution': dict(expected)})
        self.assertNotIn('EMISSIONS_UNMAPPED', {d['code'] for d in diagnostics})

    def test_all_unset_values_stay_automatic_and_unknown_keys_are_reported(self):
        result, _ = self.convert({'idleEmission': -1, 'powerEmission': -1, 'speedEmission': -1})
        self.assertEqual(result['metadata']['emissions'], {'noise': {'score': -1}, 'pollution': {'score': -1}})
        _, diagnostics = self.convert({'idleEmission': 5, 'mystery': 3})
        self.assertIn('EMISSIONS_UNMAPPED', {d['code'] for d in diagnostics})


class PlaceholderTextureTests(unittest.TestCase):
    def test_tpf2_placeholder_names_point_at_the_matching_tpf3_file(self):
        self.assertEqual(placeholder_texture('unknown_albedo_1k.dds'), '::/placeholders/mat/tex/unknown_albedo_1k.dds')
        self.assertEqual(placeholder_texture('Models/Shared/Unknown_Texture.DDS'), '::/placeholders/mat/tex/unknown_texture.dds')
        self.assertIsNone(placeholder_texture('jr_cab.dds'))
        value = rewrite_refs({'fileName': 'unknown_albedo_1k.dds'}, 'res/models/material/a.mtl', [], {}, 'mod')
        self.assertEqual(value, {'fileName': '::/placeholders/mat/tex/unknown_albedo_1k.dds'})

    def test_the_mods_own_file_of_that_name_wins(self):
        files = ['res/textures/unknown_albedo_1k.dds']
        value = rewrite_refs('unknown_albedo_1k.dds', 'res/models/material/a.mtl', files,
                             {'res/textures/unknown_albedo_1k.dds': '_assets/texture/unknown_albedo_1k.dds'}, 'mod')
        self.assertEqual(value, '/_assets/texture/unknown_albedo_1k.dds')

    def test_a_material_using_one_converts_without_an_unresolved_reference(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / 'mod'
            fixture(root)
            material = root / 'res/models/material/vehicle/train/unit/plain.mtl'
            data = parse(material.read_text('utf-8'))
            data['params']['map_albedo'] = {'fileName': 'unknown_albedo_1k.dds', 'type': 'TWOD'}
            material.write_text(dumps(data), 'utf-8')
            result = convert(root, Path(td) / 'out', siblings=None)
            self.assertEqual(result['status'], 'success', [d for d in result['diagnostics'] if d['severity'] == 'error'])
            self.assertNotIn('UNRESOLVED_REFERENCE', {d['code'] for d in result['diagnostics'] if d['severity'] == 'error'})
            written = next(Path(result['package_dir']).rglob('plain.mtl')).read_text('utf-8')
            self.assertIn('::/placeholders/mat/tex/unknown_albedo_1k.dds', written)
            self.assertTrue(validate(result['package_dir'])['valid'])


class Bc7Tests(unittest.TestCase):
    def test_bc7_is_kept_with_a_warning_for_normal_and_opaque_roles(self):
        with tempfile.TemporaryDirectory() as td:
            source = Path(td) / 'in.dds'
            bc7_dds(source)
            for role in ('normal', 'opaque_albedo', 'metal_gloss_ao'):
                target = Path(td) / f'{role}.dds'
                result = convert_dds(source, target, role)
                self.assertEqual(result.get('status'), 'success', result['diagnostics'])
                self.assertEqual(result['format_after'], 'BC7')
                self.assertEqual(target.read_bytes(), source.read_bytes())
                self.assertEqual([(d['code'], d['severity']) for d in result['diagnostics']], [('TEXTURE_BC7_KEPT', 'warning')])


class DegenerateFaceTests(unittest.TestCase):
    def test_zero_area_triangles_are_left_out_when_building_the_far_lod(self):
        positions = [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (2.0, 2.0, 2.0)]
        blob = b''.join(struct.pack('<3f', *p) for p in positions)
        indices = struct.pack('<6I', 0, 1, 2, 3, 3, 3)  # one real triangle, one with a repeated vertex
        data = blob + indices
        definition = {'vertexAttr': {'position': {'offset': 0, 'count': len(blob), 'numComp': 3}},
                      'subMeshes': {1: {'indices': {'position': {'offset': len(blob), 'count': len(indices)}}}}}
        _, faces = _decode(definition, data)
        self.assertEqual([face['ids'] for face in faces], [[0, 1, 2]])


if __name__ == '__main__':
    unittest.main()


class HornTests(unittest.TestCase):
    def test_horn_becomes_a_clip_override_of_the_sound_set(self):
        model = tpf2_car(['ELECTRIC'])
        model['metadata']['railVehicle']['soundSet'] = {'name': 'train_electric_modern', 'horn': 'vehicle/horn.wav'}
        config = convert_model(model, 'car.mdl', [])['metadata']['soundConfig']
        self.assertEqual(config['soundSet']['name'], '::/vehicle/train/shared/sound/train_electric_modern.snd')
        self.assertEqual(array(config['effects']['horn']), ['vehicle/horn.wav'])

    def test_the_horn_file_is_packaged_and_referenced_inside_the_mod(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / 'mod'
            model = fixture(root)
            model['metadata']['railVehicle']['soundSet'] = {'name': 'train_electric_modern', 'horn': 'vehicle/horn.wav'}
            (root / 'res/models/model/vehicle/train/unit.mdl').write_text(dumps(model), 'utf-8')
            (root / 'res/audio/effects/vehicle').mkdir(parents=True)
            wav = b'RIFF' + (4).to_bytes(4, 'little') + b'WAVE'
            (root / 'res/audio/effects/vehicle/horn.wav').write_bytes(wav)
            result = convert(root, Path(td) / 'out', siblings=None)
            self.assertEqual(result['status'], 'success', [d for d in result['diagnostics'] if d['severity'] == 'error'])
            package = Path(result['package_dir'])
            converted = parse((package / 'content/vehicle/train/unit.mdl').read_text('utf-8'))
            horn = array(converted['metadata']['soundConfig']['effects']['horn'])[0]
            self.assertTrue(horn.startswith('/_assets/audio/'))
            self.assertEqual((package / 'content' / horn.lstrip('/')).read_bytes(), wav)
            self.assertTrue(validate(package)['valid'])


class FileNameTests(unittest.TestCase):
    def test_hash_goes_before_the_extension_and_mesh_and_blob_stay_a_pair(self):
        from converter.resolver import portable_path
        mesh = portable_path("res/models/mesh/Foo'Bar_lod0.msh")
        blob = portable_path("res/models/mesh/Foo'Bar_lod0.msh.blob")
        self.assertTrue(mesh.endswith('.msh') and blob == mesh + '.blob', (mesh, blob))
        model = portable_path("res/models/model/vehicle/JNR185M'_ver001.mdl")
        self.assertRegex(model, r'_[0-9a-f]{8}\.mdl$')
        self.assertEqual(portable_path('res/models/model/ok_name-1.mdl'), 'res/models/model/ok_name-1.mdl')
        self.assertEqual(portable_path('res/models/model/CON.mdl'), 'res/models/model/asset_con.mdl')

    def test_a_mod_with_apostrophes_in_its_file_names_converts_to_loadable_files(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / 'mod'
            fixture(root)
            mesh_dir = root / 'res/models/mesh/vehicle/train/unit'
            for suffix in ('', '.blob'):
                (mesh_dir / f'wheel.msh{suffix}').rename(mesh_dir / f"wheel'1.msh{suffix}")
            model_dir = root / 'res/models/model/vehicle/train'
            text = (model_dir / 'unit.mdl').read_text('utf-8').replace('unit/wheel.msh', "unit/wheel'1.msh")
            (model_dir / 'unit.mdl').unlink()
            (model_dir / "Unit'X.mdl").write_text(text, 'utf-8')
            result = convert(root, Path(td) / 'out', siblings=None)
            self.assertEqual(result['status'], 'success', [d for d in result['diagnostics'] if d['severity'] == 'error'])
            package = Path(result['package_dir'])
            content = package / 'content'
            models = [p.name for p in (content / 'vehicle').rglob('*.mdl')]
            self.assertEqual(len(models), 1)
            self.assertRegex(models[0], r'^unit_x_[0-9a-f]{8}\.mdl$')
            meshes = [p for p in content.rglob('*.msh')]
            self.assertEqual(len(meshes), 1)
            self.assertTrue(Path(str(meshes[0]) + '.blob').is_file())
            bad = [p.name for p in content.rglob('*') if p.is_file() and p.name.rsplit('.', 1)[-1] not in
                   {'mdl', 'msh', 'blob', 'mtl', 'ani', 'dds', 'tga', 'png', 'lua', 'wav'}]
            self.assertEqual(bad, [])
            self.assertTrue(validate(package)['valid'])

    def test_validator_reports_a_file_whose_extension_was_swallowed(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / 'mod'
            fixture(root)
            result = convert(root, Path(td) / 'out', siblings=None)
            package = Path(result['package_dir'])
            (package / 'content/vehicle/train/unit.mdl').rename(package / 'content/vehicle/train/unit.mdl_84c72ac5')
            codes = {d['code'] for d in validate(package)['diagnostics']}
            self.assertIn('RESOURCE_EXTENSION_LOST', codes)
