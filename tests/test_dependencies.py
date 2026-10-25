"""Assets borrowed from other MODs, own sound sets, dirt/rust materials and folder-wide conversion."""
from copy import deepcopy
from pathlib import Path
import json
import shutil
import tempfile
import unittest

from converter.adapters.tpf3_writer import convert_material
from converter.batch import batch, classify_mod, find_mods, format_table, has_vehicle_models
from converter.parsers.lua import dumps, parse
from converter.pipeline import convert, validate
from converter.resolver import array, table

from test_conversion import fixture

MESH_DIR = 'res/models/mesh/vehicle/train/unit'
WAV = b'RIFF' + (4).to_bytes(4, 'little') + b'WAVE'


def lend_meshes(addon, base):
    """Move the addon's mesh files into `base`, the way an add-on relies on its base MOD."""
    (base / MESH_DIR).mkdir(parents=True)
    for path in sorted((addon / MESH_DIR).iterdir()):
        shutil.move(str(path), str(base / MESH_DIR / path.name))
    (base / 'mod.lua').write_text('function data() return {info={name="Base pack"}} end', 'utf-8')


def tree(root):
    return {p.relative_to(root): p.read_bytes() for p in Path(root).rglob('*') if p.is_file()}


class SiblingAssetTests(unittest.TestCase):
    def pair(self, td):
        addon, base = Path(td) / 'addon', Path(td) / 'base'
        model = fixture(addon)
        lend_meshes(addon, base)
        return addon, base, model

    def test_missing_assets_come_from_the_mod_next_to_it(self):
        with tempfile.TemporaryDirectory() as td:
            addon, base, _ = self.pair(td)
            before = tree(base), tree(addon)
            result = convert(addon, Path(td) / 'out')
            self.assertEqual(result['status'], 'success', [d for d in result['diagnostics'] if d['severity'] == 'error'])
            self.assertEqual(result['reference_mods'], {'base': 2})
            codes = {d['code'] for d in result['diagnostics']}
            self.assertIn('DEPENDENCY_ASSETS_IMPORTED', codes)
            self.assertNotIn('DEPENDENCY_MOD_SUSPECTED', codes)
            package = Path(result['package_dir'])
            self.assertTrue(list((package / 'content/_assets/mesh').rglob('wheel*.msh')))
            self.assertTrue(validate(package)['valid'])
            self.assertEqual((tree(base), tree(addon)), before, 'source MODs must stay untouched')

    def test_lua_library_of_the_base_mod_is_found_by_require(self):
        """An add-on's model may `require` a library that only its base MOD ships (res/scripts/<a>/<b>.lua)."""
        with tempfile.TemporaryDirectory() as td:
            addon, base, _ = self.pair(td)
            model = addon / 'res/models/model/vehicle/train/unit.mdl'
            model.write_text('local lib = require "pack.lib"\n' + model.read_text('utf-8').replace(
                'function data()\n', 'function data()\n  local unused = lib.scale\n', 1), 'utf-8')
            library = base / 'res/scripts/pack'
            library.mkdir(parents=True)
            (library / 'lib.lua').write_text('return {scale = 1}\n', 'utf-8')
            before = tree(base), tree(addon)
            result = convert(addon, Path(td) / 'out')
            errors = [d for d in result['diagnostics'] if d['severity'] == 'error']
            self.assertEqual(result['status'], 'success', errors)
            self.assertIn('LUA_SANDBOX_EVALUATED', {d['code'] for d in result['diagnostics']})
            self.assertEqual(result['reference_mods'], {'base': 3})
            self.assertEqual((tree(base), tree(addon)), before, 'source MODs must stay untouched')

    def test_library_that_no_mod_ships_is_still_an_error_not_a_crash(self):
        with tempfile.TemporaryDirectory() as td:
            addon, _, _ = self.pair(td)
            model = addon / 'res/models/model/vehicle/train/unit.mdl'
            model.write_text('local lib = require "pack.missing"\n' + model.read_text('utf-8'), 'utf-8')
            result = convert(addon, Path(td) / 'out')
            self.assertNotEqual(result['status'], 'success')
            self.assertIn('UNSUPPORTED_LUA_EXPRESSION', {d['code'] for d in result['diagnostics']})

    def test_search_can_be_turned_off_and_the_gap_is_explained(self):
        with tempfile.TemporaryDirectory() as td:
            addon, _, _ = self.pair(td)
            result = convert(addon, Path(td) / 'out', siblings=None)
            # the parts whose meshes live in the missing base MOD are left out; the conversion itself succeeds with a warning
            self.assertEqual(result['status'], 'success')
            self.assertIn('GEOMETRY_MISSING_DROPPED', {d['code'] for d in result['diagnostics']})
            self.assertEqual(result['reference_mods'], {})
            self.assertIn('DEPENDENCY_MOD_SUSPECTED', {d['code'] for d in result['diagnostics']})

    def test_the_mod_own_asset_wins_over_a_neighbour(self):
        with tempfile.TemporaryDirectory() as td:
            addon, base, _ = self.pair(td)
            # the addon now has its own (different) copy of one mesh blob
            own = addon / MESH_DIR
            own.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(base / MESH_DIR / 'wheel.msh', own / 'wheel.msh')
            (own / 'wheel.msh.blob').write_bytes((base / MESH_DIR / 'wheel.msh.blob').read_bytes() + b'\0\0\0\0')
            result = convert(addon, Path(td) / 'out')
            self.assertEqual(result['status'], 'success')
            self.assertEqual(result['reference_mods'], {})

    def test_own_sound_set_of_a_neighbour_is_converted_with_its_audio(self):
        with tempfile.TemporaryDirectory() as td:
            addon, base, model = self.pair(td)
            (base / 'res/config/sound_set').mkdir(parents=True)
            (base / 'res/audio/effects').mkdir(parents=True)
            (base / 'res/config/sound_set/own_sound.lua').write_text(
                'local soundsetutil = require "soundsetutil"\nfunction data() local result = soundsetutil.makeSoundSet() '
                'soundsetutil.addEvent(result, "closeDoors", { "own_door.wav" }, 1.0) return result end', 'utf-8')
            (base / 'res/audio/effects/own_door.wav').write_bytes(WAV)
            model['metadata']['railVehicle']['soundSet'] = {'name': 'own_sound'}
            (addon / 'res/models/model/vehicle/train/unit.mdl').write_text(dumps(model), 'utf-8')
            result = convert(addon, Path(td) / 'out')
            self.assertEqual(result['status'], 'success', [d for d in result['diagnostics'] if d['severity'] == 'error'])
            package = Path(result['package_dir'])
            self.assertNotIn('SOUNDSET_UNMAPPED', {d['code'] for d in result['diagnostics']})
            script = (package / 'content/_assets/generated/sound/own_sound.snd.lua').read_text('utf-8')
            self.assertIn('::/scripts/soundsetutil.lua', script)
            self.assertIn('/_assets/audio/effects/own_door.wav', script)
            self.assertEqual((package / 'content/_assets/audio/effects/own_door.wav').read_bytes(), WAV)
            converted = parse((package / 'content/vehicle/train/unit.mdl').read_text('utf-8'))
            self.assertEqual(converted['metadata']['soundConfig']['soundSet']['name'], '/_assets/generated/sound/own_sound.snd')
            self.assertTrue(validate(package)['valid'])

    def test_sound_set_that_nobody_provides_is_a_warning_and_a_stock_sound_stands_in(self):
        with tempfile.TemporaryDirectory() as td:
            addon, _, model = self.pair(td)
            model['metadata']['railVehicle']['soundSet'] = {'name': 'nobody_has_this'}
            (addon / 'res/models/model/vehicle/train/unit.mdl').write_text(dumps(model), 'utf-8')
            result = convert(addon, Path(td) / 'out')
            self.assertEqual(result['status'], 'success')
            codes = {d['code']: d['severity'] for d in result['diagnostics']}
            self.assertEqual(codes.get('SOUNDSET_NOT_FOUND'), 'warning')
            converted = parse((Path(result['package_dir']) / 'content/vehicle/train/unit.mdl').read_text('utf-8'))
            stock = converted['metadata']['soundConfig']['soundSet']['name']
            self.assertTrue(stock.startswith('::/vehicle/') and stock.endswith('.snd'), stock)


class OwnSoundSetTests(unittest.TestCase):
    SCRIPT = ('local soundsetutil = require "soundsetutil"\nfunction data() local result = soundsetutil.makeSoundSet() '
              'soundsetutil.addEvent(result, "closeDoors", { "own_door.wav" }, 1.0) return result end')

    def build(self, td, set_name):
        root = Path(td) / 'mod'
        model = fixture(root)
        (root / 'res/config/sound_set').mkdir(parents=True)
        (root / 'res/audio/effects').mkdir(parents=True)
        (root / f'res/config/sound_set/{set_name}.lua').write_text(self.SCRIPT, 'utf-8')
        (root / 'res/audio/effects/own_door.wav').write_bytes(WAV)
        model['metadata']['railVehicle']['soundSet'] = {'name': set_name}
        (root / 'res/models/model/vehicle/train/unit.mdl').write_text(dumps(model), 'utf-8')
        return root

    def test_sound_set_of_the_mod_itself_is_converted_and_not_copied_as_a_file(self):
        with tempfile.TemporaryDirectory() as td:
            result = convert(self.build(td, 'my_sound'), Path(td) / 'out', siblings=None)
            self.assertEqual(result['status'], 'success', [d for d in result['diagnostics'] if d['severity'] == 'error'])
            codes = {d['code'] for d in result['diagnostics']}
            self.assertFalse(codes & {'SOUNDSET_UNMAPPED', 'SOUNDSET_NOT_FOUND', 'UNSUPPORTED_LUA_EXPRESSION', 'OUTPUT_PARSE_ERROR'})
            package = Path(result['package_dir'])
            self.assertFalse((package / 'content/_assets/other').exists())
            self.assertIn('/_assets/audio/effects/own_door.wav', (package / 'content/_assets/generated/sound/my_sound.snd.lua').read_text('utf-8'))
            self.assertTrue(validate(package)['valid'])

    def test_stock_sounds_of_tpf2_point_at_the_files_tpf3_ships(self):
        with tempfile.TemporaryDirectory() as td:
            root = self.build(td, 'my_sound')
            script = ('local soundsetutil = require "soundsetutil"\nfunction data() local result = soundsetutil.makeSoundSet() '
                      'soundsetutil.addEvent(result, "clacks", { "vehicle/clack/old/part_1.wav" }, 1.0) '
                      'soundsetutil.addEvent(result, "closeDoors", { "own_door.wav" }, 1.0) return result end')
            (root / 'res/config/sound_set/my_sound.lua').write_text(script, 'utf-8')
            result = convert(root, Path(td) / 'out', siblings=None)
            self.assertEqual(result['status'], 'success', [d for d in result['diagnostics'] if d['severity'] == 'error'])
            text = (Path(result['package_dir']) / 'content/_assets/generated/sound/my_sound.snd.lua').read_text('utf-8')
            self.assertIn('::/vehicle/train/shared/sound/clack/old/part_1.wav', text)
            self.assertIn('/_assets/audio/effects/own_door.wav', text)
            self.assertTrue(validate(result['package_dir'])['valid'])

    def test_sound_set_of_the_mod_beats_the_stock_name_it_reuses(self):
        with tempfile.TemporaryDirectory() as td:
            result = convert(self.build(td, 'train_electric_modern'), Path(td) / 'out', siblings=None)
            self.assertEqual(result['status'], 'success')
            converted = parse((Path(result['package_dir']) / 'content/vehicle/train/unit.mdl').read_text('utf-8'))
            self.assertEqual(converted['metadata']['soundConfig']['soundSet']['name'],
                             '/_assets/generated/sound/train_electric_modern.snd')


class SoundAddonTests(unittest.TestCase):
    """A MOD without rail vehicles that ships the same sound set replaces it, as when both are active in TPF2."""
    BASE = ('local soundsetutil = require "soundsetutil"\nfunction data() local result = soundsetutil.makeSoundSet() '
            'soundsetutil.addEvent(result, "closeDoors", { "own_door.wav" }, 1.0) return result end')
    ADDON = ('local soundsetutil = require "soundsetutil"\nfunction data() local result = soundsetutil.makeSoundSet() '
             'soundsetutil.addEvent(result, "closeDoors", { "own_door.wav" }, 1.0) '
             'soundsetutil.addEvent(result, "openDoors", { "extra_door.wav" }, 1.0) return result end')
    NEW_WAV = WAV + b'new'

    def library(self, td, set_name='my_sound'):
        library = Path(td) / 'workshop'
        base = library / '100_base'
        model = fixture(base)
        (base / 'res/config/sound_set').mkdir(parents=True)
        (base / 'res/audio/effects').mkdir(parents=True)
        (base / f'res/config/sound_set/{set_name}.lua').write_text(self.BASE, 'utf-8')
        (base / 'res/audio/effects/own_door.wav').write_bytes(WAV)
        model['metadata']['railVehicle']['soundSet'] = {'name': set_name}
        (base / 'res/models/model/vehicle/train/unit.mdl').write_text(dumps(model), 'utf-8')
        return library, base

    def addon(self, library, name='200_sound', set_name='my_sound'):
        root = library / name
        (root / 'res/config/sound_set').mkdir(parents=True)
        (root / 'res/audio/effects').mkdir(parents=True)
        (root / 'mod.lua').write_text('function data() return {info={name="Sound"}} end', 'utf-8')
        (root / f'res/config/sound_set/{set_name}.lua').write_text(self.ADDON, 'utf-8')
        (root / 'res/audio/effects/own_door.wav').write_bytes(self.NEW_WAV)
        (root / 'res/audio/effects/extra_door.wav').write_bytes(self.NEW_WAV)
        return root

    def sound_text(self, result):
        return (Path(result['package_dir']) / 'content/_assets/generated/sound/my_sound.snd.lua').read_text('utf-8')

    def test_sound_addon_replaces_the_sound_set_and_adds_its_recordings(self):
        with tempfile.TemporaryDirectory() as td:
            library, base = self.library(td)
            addon = self.addon(library)
            before = tree(base), tree(addon)
            result = convert(base, Path(td) / 'out')
            self.assertEqual(result['status'], 'success', [d for d in result['diagnostics'] if d['severity'] == 'error'])
            codes = {d['code']: d['severity'] for d in result['diagnostics']}
            self.assertEqual(codes.get('SOUND_SET_OVERRIDDEN_BY_ADDON'), 'info')
            self.assertIn('200_sound', result['reference_mods'])
            text = self.sound_text(result)
            self.assertIn('/_assets/audio/effects/extra_door.wav', text)
            content = Path(result['package_dir']) / 'content/_assets/audio/effects'
            self.assertEqual((content / 'own_door.wav').read_bytes(), self.NEW_WAV)
            self.assertEqual((content / 'extra_door.wav').read_bytes(), self.NEW_WAV)
            self.assertEqual((tree(base), tree(addon)), before, 'source MODs must stay untouched')

    def test_without_the_addon_the_mods_own_sound_set_is_used(self):
        with tempfile.TemporaryDirectory() as td:
            library, _ = self.library(td)
            result = convert(library / '100_base', Path(td) / 'out')
            self.assertNotIn('SOUND_SET_OVERRIDDEN_BY_ADDON', {d['code'] for d in result['diagnostics']})
            self.assertNotIn('extra_door.wav', self.sound_text(result))
            self.assertEqual((Path(result['package_dir']) / 'content/_assets/audio/effects/own_door.wav').read_bytes(), WAV)

    def test_search_off_keeps_the_mods_own_sound_set(self):
        with tempfile.TemporaryDirectory() as td:
            library, base = self.library(td)
            self.addon(library)
            result = convert(base, Path(td) / 'out', siblings=None)
            self.assertNotIn('extra_door.wav', self.sound_text(result))

    def test_another_vehicle_mod_with_the_same_sound_set_is_not_an_override(self):
        with tempfile.TemporaryDirectory() as td:
            library, base = self.library(td)
            repaint = Path(td) / 'workshop' / '300_repaint'
            fixture(repaint)
            (repaint / 'res/config/sound_set').mkdir(parents=True)
            (repaint / 'res/config/sound_set/my_sound.lua').write_text(self.ADDON, 'utf-8')
            result = convert(base, Path(td) / 'out')
            self.assertNotIn('SOUND_SET_OVERRIDDEN_BY_ADDON', {d['code'] for d in result['diagnostics']})
            self.assertNotIn('extra_door.wav', self.sound_text(result))

    def test_sound_set_the_models_do_not_use_is_left_alone(self):
        with tempfile.TemporaryDirectory() as td:
            library, base = self.library(td)
            self.addon(library, set_name='other_sound')
            result = convert(base, Path(td) / 'out')
            self.assertNotIn('SOUND_SET_OVERRIDDEN_BY_ADDON', {d['code'] for d in result['diagnostics']})

    def test_two_competing_addons_are_reported_and_nothing_is_replaced(self):
        with tempfile.TemporaryDirectory() as td:
            library, base = self.library(td)
            self.addon(library, '200_sound')
            self.addon(library, '210_sound')
            result = convert(base, Path(td) / 'out')
            codes = {d['code']: d['severity'] for d in result['diagnostics']}
            self.assertEqual(codes.get('SOUND_SET_OVERRIDE_AMBIGUOUS'), 'warning')
            self.assertNotIn('SOUND_SET_OVERRIDDEN_BY_ADDON', codes)
            self.assertNotIn('extra_door.wav', self.sound_text(result))

    def test_batch_applies_it_in_parallel_too(self):
        with tempfile.TemporaryDirectory() as td:
            library, _ = self.library(td)
            self.addon(library)
            second = library / '150_other'
            fixture(second)
            result = batch(library, Path(td) / 'out', mod_workers=2)
            rows = {r['folder']: r for r in result['batch']['mods']}
            self.assertEqual({k: v['status'] for k, v in rows.items()},
                             {'100_base': 'success', '150_other': 'success', '200_sound': 'skipped'})
            self.assertIn('200_sound', rows['100_base']['reference_mods'])
            package = Path(rows['100_base']['package_dir'])
            self.assertIn('extra_door.wav', (package / 'content/_assets/generated/sound/my_sound.snd.lua').read_text('utf-8'))


class NonRailModelTests(unittest.TestCase):
    def test_props_and_road_vehicles_in_a_rail_mod_are_left_out_without_an_error(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / 'mod'
            fixture(root)
            bus = {'version': 1, 'lods': table([{'node': {'name': 'RootNode'}, 'visibleFrom': 0, 'visibleTo': 100}]),
                   'metadata': {'roadVehicle': {'configs': {}}, 'transportVehicle': {'carrier': 'ROAD'}}}
            (root / 'res/models/model/vehicle/bus').mkdir(parents=True)
            (root / 'res/models/model/vehicle/bus/city.mdl').write_text(dumps(bus), 'utf-8')
            result = convert(root, Path(td) / 'out', siblings=None)
            self.assertEqual(result['status'], 'success', [d for d in result['diagnostics'] if d['severity'] == 'error'])
            codes = {d['code']: d['severity'] for d in result['diagnostics']}
            self.assertEqual(codes.get('NON_RAIL_MODELS_SKIPPED'), 'info')
            self.assertNotIn('MODEL_KIND_UNSUPPORTED', codes)
            models = [p.name for p in (Path(result['package_dir']) / 'content/vehicle').rglob('*.mdl')]
            self.assertEqual(models, ['unit.mdl'])


class MaterialTests(unittest.TestCase):
    def test_dirt_and_rust_overlays_use_the_tpf3_sampler_slots(self):
        sampler = {'fileName': 'dirt.dds', 'type': 'TWOD', 'redGreen': True}
        material = {'type': 'PHYSICAL', 'params': {name: deepcopy(sampler) for name in
                    ('map_dirt', 'map_dirt_normal', 'map_rust', 'map_rust_normal')}}
        diagnostics = []
        params = convert_material(material, 'm.mtl', diagnostics)['params']
        for name, slot in (('map_dirt', 'dirtTex'), ('map_dirt_normal', 'dirtNormalTex'),
                           ('map_rust', 'rustTex'), ('map_rust_normal', 'rustNormalTex')):
            self.assertEqual(params[name], {'fragmentSamplers': {slot: sampler}})
        self.assertFalse([d for d in diagnostics if d['code'] == 'MATERIAL_PARAMETER_UNMAPPED'])


    def test_the_order_of_a_material_moves_beside_params_without_an_error(self):
        diagnostics = []
        material = convert_material({'type': 'PHYSICAL', 'params': {'order': 2}}, 'm.mtl', diagnostics)
        self.assertEqual(material['order'], 2)
        self.assertNotIn('order', material['params'])
        self.assertEqual([d['code'] for d in diagnostics], [])
        # an order that is already beside params wins
        kept = convert_material({'type': 'PHYSICAL', 'order': 0, 'params': {'order': 2}}, 'm.mtl', [])
        self.assertEqual(kept['order'], 0)

    def test_emissive_lamps_get_an_always_on_window_but_keep_one_that_is_given(self):
        lamp = {'type': 'EMISSIVE', 'params': {'emissive_scale': {'emissiveScale': {1: 5, 2: 5, 3: 5}}}}
        entry = convert_material(lamp, 'lamp.mtl', [])['params']['emissive_scale']['fragmentProperties'][1]
        self.assertEqual((entry['fromTime'], entry['toTime']), (0, 24))
        self.assertEqual(entry['emissiveScale'], {1: 5, 2: 5, 3: 5})
        night = {'type': 'EMISSIVE', 'params': {'emissive_scale': {'emissiveScale': {1: 1, 2: 1, 3: 1}, 'fromTime': 18, 'toTime': 6}}}
        entry = convert_material(night, 'lamp.mtl', [])['params']['emissive_scale']['fragmentProperties'][1]
        self.assertEqual((entry['fromTime'], entry['toTime']), (18, 6))


class BatchTests(unittest.TestCase):
    def library(self, td):
        library = Path(td) / 'workshop'
        library.mkdir()
        addon, base = library / '200_addon', library / '100_base'
        fixture(addon)
        lend_meshes(addon, base)
        # base pack: a vehicle of its own, using the meshes it ships
        shutil.copytree(addon / 'res/models/material', base / 'res/models/material')
        shutil.copytree(addon / 'res/models/animation', base / 'res/models/animation')
        shutil.copyfile(addon / 'res/models/model/vehicle/train/unit.mdl', base / 'res/models/model_unit.tmp')
        (base / 'res/models/model/vehicle/train').mkdir(parents=True)
        shutil.move(str(base / 'res/models/model_unit.tmp'), str(base / 'res/models/model/vehicle/train/unit.mdl'))
        scenery = library / '300_scenery'
        (scenery / 'res/models/model/building').mkdir(parents=True)
        (scenery / 'mod.lua').write_text('function data() return {info={name="Scenery"}} end', 'utf-8')
        (scenery / 'res/models/model/building/house.mdl').write_text('function data() return {metadata={}} end', 'utf-8')
        (library / 'texture_cache').mkdir()
        (library / 'not_a_mod').mkdir()
        return library

    def test_find_mods_and_vehicle_filter(self):
        with tempfile.TemporaryDirectory() as td:
            library = self.library(td)
            self.assertEqual([m.name for m in find_mods(library)], ['100_base', '200_addon', '300_scenery'])
            self.assertEqual([has_vehicle_models(m) for m in find_mods(library)], [True, True, False])
            self.assertEqual(find_mods(library / '200_addon'), [library / '200_addon'])

    def test_every_vehicle_mod_is_converted_and_a_summary_is_written(self):
        with tempfile.TemporaryDirectory() as td:
            library = self.library(td)
            result = batch(library, Path(td) / 'out')
            summary = result['batch']
            rows = {r['folder']: r for r in summary['mods']}
            self.assertEqual({k: v['status'] for k, v in rows.items()},
                             {'100_base': 'success', '200_addon': 'success', '300_scenery': 'skipped'})
            self.assertEqual(rows['200_addon']['reference_mods'], {'100_base': 2})
            self.assertEqual(result['status'], 'success')
            run = Path(result['run_dir'])
            written = json.loads((run / 'batch_summary.json').read_text('utf-8'))
            self.assertEqual(written['counts'], {'skipped': 1, 'success': 2})
            self.assertIn('200_addon', (run / 'batch_summary.md').read_text('utf-8'))
            for name in ('100_base', '200_addon'):
                self.assertTrue(Path(rows[name]['package_dir'], 'mod.json').is_file())

    def test_one_broken_mod_does_not_stop_the_others(self):
        with tempfile.TemporaryDirectory() as td:
            library = self.library(td)
            broken = library / '150_broken'
            fixture(broken)
            (broken / 'res/models/model/vehicle/train/unit.mdl').write_text('function data() return {railVehicle = os.execute("x")} end', 'utf-8')
            result = batch(library, Path(td) / 'out')
            statuses = {r['folder']: r['status'] for r in result['batch']['mods']}
            self.assertIn(statuses['150_broken'], {'partial', 'failed'})
            self.assertEqual(statuses['200_addon'], 'success')
            self.assertEqual(result['status'], 'partial')

    def test_only_and_search_switch(self):
        with tempfile.TemporaryDirectory() as td:
            library = self.library(td)
            result = batch(library, Path(td) / 'out', only=['200_addon'], search_siblings=False)
            self.assertEqual([r['folder'] for r in result['batch']['mods']], ['200_addon'])
            self.assertEqual(result['batch']['mods'][0]['status'], 'success')

    def test_output_inside_the_source_folder_is_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            library = self.library(td)
            with self.assertRaises(ValueError):
                batch(library, library / 'out')
            with self.assertRaises(ValueError):
                batch(Path(td) / 'empty_folder_does_not_exist', Path(td) / 'out')


class ClassificationTests(unittest.TestCase):
    def mod(self, root, files):
        for rel, text in files.items():
            path = Path(root) / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, 'utf-8')
        (Path(root) / 'mod.lua').write_text('function data() return {info={name="X"}} end', 'utf-8')
        return Path(root)

    def test_script_only_mod_is_not_applicable(self):
        with tempfile.TemporaryDirectory() as td:
            mod = self.mod(Path(td) / 's', {'res/scripts/timetable.lua': 'x', 'res/config/game_script/gs.lua': 'x'})
            kind = classify_mod(mod)
            self.assertFalse(kind['supported'])
            self.assertIn('スクリプト', kind['reason'])
            self.assertTrue(kind['reason'].startswith('対象外'))

    def test_empty_and_texture_only_mods_are_not_applicable(self):
        with tempfile.TemporaryDirectory() as td:
            kind = classify_mod(self.mod(Path(td) / 'e', {'res/textures/x.dds': 'x'}))
            self.assertFalse(kind['supported'])
            self.assertIn('鉄道車両がありません', kind['reason'])

    def test_road_vehicles_ships_buildings_and_streets_are_named(self):
        with tempfile.TemporaryDirectory() as td:
            mod = self.mod(Path(td) / 'r', {
                'res/models/model/vehicle/bus/a.mdl': 'metadata = { roadVehicle = {} }',
                'res/models/model/vehicle/ship/b.mdl': 'metadata = { waterVehicle = {} }',
                'res/models/model/building/c.mdl': 'metadata = { }',
                'res/construction/station/d.con': 'x', 'res/config/street/e.lua': 'x'})
            kind = classify_mod(mod)
            self.assertFalse(kind['supported'])
            for word in ('道路車両 1', '船 1', '車両以外のモデル 1', '建物・施設', '道路'):
                self.assertIn(word, kind['reason'])

    def test_rail_mod_with_other_content_is_converted_and_the_rest_is_listed(self):
        with tempfile.TemporaryDirectory() as td:
            mod = self.mod(Path(td) / 'm', {
                'res/models/model/vehicle/train/a.mdl': 'metadata = { railVehicle = {} }',
                'res/models/model/vehicle/bus/b.mdl': 'metadata = { roadVehicle = {} }',
                'res/config/multiple_unit/x.lua': 'x', 'res/config/sound_set/y.lua': 'x'})
            kind = classify_mod(mod)
            self.assertTrue(kind['supported'])
            self.assertEqual(kind['rail_models'], 1)
            self.assertEqual(kind['contents'], ['道路車両 1'])

    def test_batch_lists_skipped_mods_with_reasons(self):
        with tempfile.TemporaryDirectory() as td:
            library = Path(td) / 'lib'
            fixture(library / '10_vehicle')
            self.mod(library / '20_script', {'res/scripts/a.lua': 'x'})
            self.mod(library / '30_bus', {'res/models/model/vehicle/bus/a.mdl': 'metadata = { roadVehicle = {} }'})
            result = batch(library, Path(td) / 'out')
            rows = {r['folder']: r for r in result['batch']['mods']}
            self.assertEqual({k: v['status'] for k, v in rows.items()},
                             {'10_vehicle': 'success', '20_script': 'skipped', '30_bus': 'skipped'})
            self.assertIn('スクリプト', rows['20_script']['reason'])
            self.assertIn('道路車両', rows['30_bus']['reason'])
            table = format_table(result['batch'])
            self.assertIn('対象外', table)
            self.assertIn('10_vehicle', table)
            self.assertIn('成功 1', table)
            self.assertEqual(result['status'], 'success')  # skipping non-vehicle MODs is not a problem


if __name__ == '__main__':
    unittest.main()
