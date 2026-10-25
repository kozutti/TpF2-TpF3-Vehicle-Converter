"""Mistakes in MODs that TPF2 tolerates: misspelt names, missing textures, groups, labels, odd sound scripts."""
from pathlib import Path
import json
import tempfile
import unittest

from converter.parsers.lua import LuaParseError, dumps, parse
from converter.pipeline import convert, validate

from test_conversion import fixture


class LenientLuaTests(unittest.TestCase):
    def test_an_undefined_global_is_nil_and_reported_when_asked_to(self):
        undefined = []
        data = parse('function data()\n  return { a = falsee, b = 1 }\nend', undefined=undefined)
        self.assertEqual(data, {'b': 1})
        self.assertEqual(undefined, [('falsee', 2)])

    def test_strict_by_default_and_other_errors_stay_errors(self):
        with self.assertRaises(LuaParseError):
            parse('function data() return { a = falsee } end')
        with self.assertRaises(LuaParseError):
            parse('function data() return { a = "x" .. missing } end', undefined=[])
        with self.assertRaises(LuaParseError):
            parse('function data() return { a = os.time } end', undefined=[])


class SloppyModTests(unittest.TestCase):
    def prepared(self, directory):
        root = Path(directory) / 'mod'
        fixture(root)
        material = root / 'res/models/material/vehicle/train/unit/plain.mtl'
        text = material.read_text('utf-8')
        data = parse(text)
        data['params']['map_normal'] = {'fileName': 'models/vehicle/train/nonomal.dds', 'type': 'TWOD'}
        data['params']['map_dirt'] = {'fileName': 'models/vehicle/train/dirt_missing.dds', 'type': 'TWOD'}
        material.write_text(dumps(data).replace('params = {', 'params = { junk = falsee,', 1), 'utf-8')
        return root

    def test_misspelt_names_and_missing_textures_do_not_stop_the_conversion(self):
        with tempfile.TemporaryDirectory() as td:
            root = self.prepared(td)
            result = convert(root, Path(td) / 'out', siblings=None)
            errors = [d for d in result['diagnostics'] if d['severity'] == 'error']
            self.assertEqual(result['status'], 'success', errors)
            codes = {d['code'] for d in result['diagnostics']}
            self.assertTrue({'LUA_UNDEFINED_NAME', 'TEXTURE_MISSING', 'TEXTURE_MISSING_DROPPED'} <= codes, codes)
            written = next(Path(result['package_dir']).rglob('plain.mtl')).read_text('utf-8')
            self.assertIn('::/placeholders/mat/tex/default_normal_map.dds', written)
            self.assertNotIn('dirt_missing', written)
            self.assertNotIn('falsee', written)
            self.assertTrue(validate(result['package_dir'])['valid'])

    def vehicle_model(self, root):
        return root / 'res/models/model/vehicle/train/unit.mdl'

    def test_label_list_and_a_model_that_is_its_own_group_are_carried_over(self):
        with tempfile.TemporaryDirectory() as td:
            root = self.prepared(td)
            path = self.vehicle_model(root)
            data = parse(path.read_text('utf-8'))
            data['metadata']['labelList'] = {'labels': {1: {'transf': {i: float(i == 1 or i == 6 or i == 11 or i == 16) for i in range(1, 17)},
                                                            'size': {1: 1.0, 2: 0.2}, 'nLines': 1, 'alpha': 1.0}}}
            data['metadata']['transportVehicle']['groupFileName'] = 'vehicle/train/unit.mdl'
            path.write_text(dumps(data), 'utf-8')
            result = convert(root, Path(td) / 'out', siblings=None)
            codes = {d['code'] for d in result['diagnostics']}
            self.assertEqual(result['status'], 'success', [d for d in result['diagnostics'] if d['severity'] == 'error'])
            self.assertNotIn('METADATA_UNMAPPED', codes)
            self.assertNotIn('VEHICLE_GROUP_UNMAPPED', codes)
            model = parse(next(Path(result['package_dir']).glob('content/vehicle/**/*.mdl')).read_text('utf-8'))
            self.assertIn('labelList', model['metadata'])
            self.assertNotIn('groupFileName', model['metadata']['transportVehicle'])

    def test_a_group_that_the_mod_does_not_contain_leaves_the_vehicle_ungrouped_with_a_warning(self):
        with tempfile.TemporaryDirectory() as td:
            root = self.prepared(td)
            path = self.vehicle_model(root)
            data = parse(path.read_text('utf-8'))
            data['metadata']['transportVehicle']['groupFileName'] = 'vehicle/train/nowhere/menu.mdl'
            path.write_text(dumps(data), 'utf-8')
            result = convert(root, Path(td) / 'out', siblings=None)
            self.assertEqual(result['status'], 'success', [d for d in result['diagnostics'] if d['severity'] == 'error'])
            group = [d for d in result['diagnostics'] if d['code'] == 'VEHICLE_GROUP_UNMAPPED']
            self.assertEqual([d['severity'] for d in group], ['warning'])

    def test_an_unreadable_sound_script_means_a_stock_sound_not_a_failed_conversion(self):
        with tempfile.TemporaryDirectory() as td:
            root = self.prepared(td)
            path = self.vehicle_model(root)
            data = parse(path.read_text('utf-8'))
            data['metadata']['railVehicle']['soundSet'] = {'name': 'my_odd_sound'}
            path.write_text(dumps(data), 'utf-8')
            sounds = root / 'res/config/sound_set'
            sounds.mkdir(parents=True)
            (sounds / 'my_odd_sound.lua').write_text('function data()\n  return { name = "x" }\nend\n', 'utf-8')
            result = convert(root, Path(td) / 'out', siblings=None)
            errors = [d for d in result['diagnostics'] if d['severity'] == 'error']
            self.assertEqual(result['status'], 'success', errors)
            self.assertIn('GAME_SOUNDSET_UNSUPPORTED', {d['code'] for d in result['diagnostics']})
            model = parse(next(Path(result['package_dir']).glob('content/vehicle/**/*.mdl')).read_text('utf-8'))
            self.assertTrue(model['metadata']['soundConfig']['soundSet']['name'].startswith('::/vehicle/'))
            self.assertTrue(validate(result['package_dir'])['valid'])


class TranslationFileTests(unittest.TestCase):
    STRINGS = ('function removeIndent(str)\nreturn (str:gsub("\\n%s+", "\\n"))\nend\n\n'
               'function data()\nreturn {\n  ja = {\n    ["modname"] = "テスト車両",\n    ["desc"] = removeIndent("a\\n   b"),\n  },\n}\nend\n')

    def test_a_translation_file_with_a_helper_function_still_names_the_mod(self):
        from converter.parsers import lua_sandbox
        if not lua_sandbox.available():
            self.skipTest('lupa is not installed')
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / 'mod'
            fixture(root)
            (root / 'mod.lua').write_text(
                'function data() return {info={name=_("modname"),description=_("desc"),authors={{name="A",role="CREATOR"}}}} end', 'utf-8')
            (root / 'strings.lua').write_text(self.STRINGS, 'utf-8')
            result = convert(root, Path(td) / 'out', siblings=None)
            self.assertEqual(result['status'], 'success')
            info = json.loads((Path(result['package_dir']) / '_metadata' / 'modinfo.json').read_text('utf-8'))
            self.assertEqual(info['name'], 'テスト車両')
            self.assertEqual(info['description'], 'a\nb')
            self.assertEqual(info['localization']['ja']['name'], 'テスト車両')

    def test_the_batch_title_reads_the_same_file(self):
        from converter.batch import mod_title
        from converter.parsers import lua_sandbox
        if not lua_sandbox.available():
            self.skipTest('lupa is not installed')
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / 'mod.lua').write_text('function data() return {info={name=_("modname")}} end', 'utf-8')
            (Path(td) / 'strings.lua').write_text(self.STRINGS, 'utf-8')
            self.assertEqual(mod_title(td), 'テスト車両')

class GroupParentTests(unittest.TestCase):
    def test_a_group_model_whose_own_group_path_is_wrong_stays_the_parent(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / 'mod'
            fixture(root)
            path = root / 'res/models/model/vehicle/train/unit.mdl'
            data = parse(path.read_text('utf-8'))
            parent = parse(path.read_text('utf-8'))
            parent['metadata']['transportVehicle']['groupFileName'] = 'vehicle/train/typo/unit_menu.mdl'
            (root / 'res/models/model/vehicle/train/unit_menu.mdl').write_text(dumps(parent), 'utf-8')
            data['metadata']['transportVehicle']['groupFileName'] = 'vehicle/train/unit_menu.mdl'
            path.write_text(dumps(data), 'utf-8')
            result = convert(root, Path(td) / 'out', siblings=None)
            codes = {d['code'] for d in result['diagnostics']}
            self.assertEqual(result['status'], 'success', [d for d in result['diagnostics'] if d['severity'] == 'error'])
            self.assertIn('VEHICLE_GROUP_SELF_PARENT', codes)
            self.assertNotIn('VEHICLE_GROUP_UNMAPPED', codes)
            menu = parse(next(Path(result['package_dir']).glob('content/vehicle/**/unit_menu.mdl')).read_text('utf-8'))
            self.assertNotIn('groupFileName', menu['metadata']['transportVehicle'])


class UndefinedNameKeyTests(unittest.TestCase):
    def test_a_model_name_key_the_mod_never_defines_uses_the_translation_that_matches_the_file_name(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / 'mod'
            fixture(root)
            path = root / 'res/models/model/vehicle/train/unit.mdl'
            data = parse(path.read_text('utf-8'))
            data['metadata']['description'] = {'name': 'name_model_typo', 'description': ''}
            path.write_text(dumps(data), 'utf-8')
            (root / 'strings.lua').write_text('function data() return { ja = { ["modname"] = "M", ["unit"] = "ユニット車", ["name_x"] = "NG" } } end', 'utf-8')
            result = convert(root, Path(td) / 'out', siblings=None)
            self.assertEqual(result['status'], 'success')
            model = parse(next(Path(result['package_dir']).glob('content/vehicle/**/unit.mdl')).read_text('utf-8'))
            self.assertEqual(model['metadata']['description']['name'], 'ユニット車')
            self.assertIn('NAME_KEY_UNDEFINED_GUESSED', {d['code'] for d in result['diagnostics']})


class EmptyNameTests(unittest.TestCase):
    """MOD 9000000007 (JNR185): `name = _("")` left the rows of the parts list without a name."""
    def test_an_empty_vehicle_name_becomes_the_model_file_name(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / 'mod'
            fixture(root)
            path = root / 'res/models/model/vehicle/train/unit.mdl'
            data = parse(path.read_text('utf-8'))
            data['metadata']['description'] = {'name': '', 'description': ''}
            path.write_text(dumps(data), 'utf-8')
            result = convert(root, Path(td) / 'out', siblings=None)
            model = parse(next(Path(result['package_dir']).glob('content/vehicle/**/unit.mdl')).read_text('utf-8'))
            self.assertEqual(model['metadata']['description']['name'], 'unit')
            self.assertIn('NAME_EMPTY_FILE_NAME', {d['code'] for d in result['diagnostics']})


class FormationNameTests(unittest.TestCase):
    """E8 series (MOD 9000000010): the cars 11-17 name `_("e8_12")` etc., which strings.lua never defines, so the raw key showed
    in the depot. The multiple-unit config that lists the car has a translated name and the car's position."""
    def converted_name(self, positions):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / 'mod'
            fixture(root)
            path = root / 'res/models/model/vehicle/train/unit.mdl'
            data = parse(path.read_text('utf-8'))
            data['metadata']['description'] = {'name': 'e8_12', 'description': ''}
            path.write_text(dumps(data), 'utf-8')
            (root / 'strings.lua').write_text('function data() return { ja = { ["modname"] = "M", ["set_name"] = "【7両】特急" } } end', 'utf-8')
            entries = ' '.join('{ name = "vehicle/train/unit.mdl", forward = true },' for _ in positions)
            (root / 'res/config/multiple_unit').mkdir(parents=True, exist_ok=True)
            (root / 'res/config/multiple_unit/set.lua').write_text(
                f'function data() return {{ vehicles = {{ {entries} }}, name = _("set_name"), desc = "d" }} end', 'utf-8')
            result = convert(root, Path(td) / 'out', siblings=None)
            model = parse(next(Path(result['package_dir']).glob('content/vehicle/**/unit.mdl')).read_text('utf-8'))
            return model['metadata']['description']['name'], {d['code'] for d in result['diagnostics']}

    def test_an_undefined_name_key_becomes_the_formation_name_and_position(self):
        name, codes = self.converted_name([1])
        self.assertEqual(name, '【7両】特急 1両目')
        self.assertIn('NAME_FROM_FORMATION', codes)
        self.assertNotIn('NAME_KEY_UNDEFINED', codes)

    def test_a_car_listed_at_several_positions_gets_all_of_them(self):
        name, _ = self.converted_name([1, 1])
        self.assertEqual(name, '【7両】特急 1・2両目')


class LabelAnchorTests(SloppyModTests):
    def convert_with_label(self, with_body):
        with tempfile.TemporaryDirectory() as td:
            root = self.prepared(td)
            path = self.vehicle_model(root)
            data = parse(path.read_text('utf-8'))
            label = {'transf': {i: float(i in (1, 6, 11, 16)) for i in range(1, 17)}, 'size': {1: 1.0, 2: 0.2}, 'nLines': 1, 'alpha': 1.0, 'childId': ''}
            data['metadata']['labelList'] = {'labels': {1: label}}
            if with_body:
                lod_root = data['lods'][1]['node']
                children = lod_root['children']
                children[len(children) + 1] = {'name': 'body', 'mesh': 'vehicle/train/unit/wheel.msh',
                                               'materials': {1: 'vehicle/train/unit/plain.mtl'},
                                               'transf': {i: float(i in (1, 6, 11, 16)) for i in range(1, 17)}}
            path.write_text(dumps(data), 'utf-8')
            result = convert(root, Path(td) / 'out', siblings=None)
            self.assertEqual(result['status'], 'success', [d for d in result['diagnostics'] if d['severity'] == 'error'])
            model = parse(next(Path(result['package_dir']).glob('content/vehicle/**/*.mdl')).read_text('utf-8'))
            labels = list(model['metadata']['labelList']['labels'].values())
            return labels, {d['code'] for d in result['diagnostics']}

    def test_labels_on_the_root_move_to_the_body_node_so_they_follow_cant(self):
        labels, codes = self.convert_with_label(True)
        self.assertEqual([entry['childId'] for entry in labels], ['body'])
        self.assertIn('LABELS_ATTACHED_TO_BODY', codes)

    def test_without_a_body_node_labels_name_the_root_so_the_cant_mod_can_move_them(self):
        labels, codes = self.convert_with_label(False)
        self.assertEqual([entry.get('childId') for entry in labels], ['RootNode'])
        self.assertIn('LABELS_ATTACHED_TO_ROOT', codes)


class AuthorPrefixedNameKeyTests(unittest.TestCase):
    def converted_name(self, key, stem='unit'):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / 'mod'
            fixture(root)
            models = root / 'res/models/model/vehicle/train'
            path = models / 'unit.mdl'
            data = parse(path.read_text('utf-8'))
            data['metadata']['description'] = {'name': key, 'description': ''}
            if stem != 'unit':
                (models / f'{stem}.mdl').write_text(dumps(data), 'utf-8')
                path.unlink()
            else:
                path.write_text(dumps(data), 'utf-8')
            (root / 'strings.lua').write_text('function data() return { ja = { ["modname"] = "M", ["other"] = "別" } } end', 'utf-8')
            result = convert(root, Path(td) / 'out', siblings=None)
            self.assertEqual(result['status'], 'success')
            model = parse(next(Path(result['package_dir']).glob(f'content/vehicle/**/{stem}.mdl')).read_text('utf-8'))
            return model['metadata']['description']['name'], {d['code'] for d in result['diagnostics']}

    def test_an_author_tag_in_front_of_the_file_name_is_dropped(self):
        name, codes = self.converted_name('Pz7_OER_M4000_2', 'OER_M4000_2_RExp')
        self.assertEqual(name, 'OER_M4000_2_RExp')
        self.assertIn('NAME_KEY_UNDEFINED_FILE_NAME', codes)

    def test_a_key_that_is_the_intended_name_is_kept(self):
        name, codes = self.converted_name('KiHa180', 'kiha180')
        self.assertEqual(name, 'KiHa180')
        self.assertIn('NAME_KEY_UNDEFINED', codes)

    def test_a_numeric_file_name_would_say_less_than_the_key(self):
        name, _ = self.converted_name('n700_01', '01')
        self.assertEqual(name, 'n700_01')


if __name__ == '__main__':
    unittest.main()
