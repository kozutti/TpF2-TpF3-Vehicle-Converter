from copy import deepcopy
from pathlib import Path
import tempfile
import unittest

from converter.adapters.tpf3_writer import convert_model, rail_transport_modes
from converter.parsers.lua import parse, dumps
from converter.resolver import array, table
from converter.transforms.singles import apply_single_vehicle_mode, group_placeholder
from converter import pipeline

IDENTITY = table([1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1])


def tpf2_car(engine_types, multiple_unit_only=True):
    transport = {'carrier': 'RAIL', 'loadSpeed': 4, 'reversible': True,
                 'compartmentsList': {1: {'loadConfigs': {1: {'cargoEntries': {1: {'type': 'PASSENGERS', 'capacity': 80, 'seats': {}}},
                                                              'toHide': {}}}}}}
    if multiple_unit_only:
        transport['multipleUnitOnly'] = True
    engines = {i: {'type': kind, 'power': 100, 'tractiveEffort': 10} for i, kind in enumerate(engine_types, 1)}
    return {'version': 1, 'boundingInfo': {'bbMax': table([5, 1, 3]), 'bbMin': table([-5, -1, 0])},
            'lods': {1: {'node': {'name': 'RootNode', 'transf': IDENTITY}, 'visibleFrom': 0, 'visibleTo': 1000}},
            'metadata': {'railVehicle': {'configs': {1: {}}, 'engines': engines, 'weight': 30, 'topSpeed': 30},
                         'transportVehicle': transport}}


class TransportModeTests(unittest.TestCase):
    def modes(self, engine_types):
        transport = convert_model(tpf2_car(engine_types), 'car.mdl', [])['metadata']['transportVehicle']
        return array(transport['transportModes']), array(transport['engineTransportModes']), transport

    def test_electric_only_vehicle_needs_catenary(self):
        modes, engine_modes, transport = self.modes(['ELECTRIC'])
        self.assertEqual(modes, ['TRAIN', 'ELECTRIC_TRAIN'])
        self.assertEqual(engine_modes, ['ELECTRIC_TRAIN'])
        self.assertEqual(array(transport['tags']), modes)
        self.assertEqual(array(transport['engineTags']), engine_modes)

    def test_diesel_and_steam_use_only_names_the_game_knows(self):
        for kind in ('DIESEL', 'STEAM'):
            modes, engine_modes, _ = self.modes([kind])
            self.assertEqual(engine_modes, ['TRAIN', 'ELECTRIC_TRAIN'])
            self.assertFalse(any(name.startswith(('DIESEL', 'STEAM')) for name in modes + engine_modes))

    def test_vehicle_without_engine_has_no_engine_modes(self):
        modes, engine_modes, _ = self.modes([])
        self.assertEqual(modes, ['TRAIN', 'ELECTRIC_TRAIN'])
        self.assertEqual(engine_modes, [])

    def test_helper_mixed_engines(self):
        self.assertEqual(array(rail_transport_modes({'ELECTRIC', 'DIESEL'})[1]), ['TRAIN', 'ELECTRIC_TRAIN'])

    def test_brake_deceleration_is_written(self):
        land = convert_model(tpf2_car(['ELECTRIC']), 'car.mdl', [])['metadata']['landVehicle']
        self.assertEqual(land['brakeDeceleration'], 2.5)


def write_model(content, path, data):
    target = content / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(dumps(data), encoding='utf-8')


def read_model(content, path):
    return parse((content / path).read_text('utf-8-sig'))


class SingleVehicleModeTests(unittest.TestCase):
    def package(self, root, layout):
        """layout: {relative model path: engine types}; every car is multipleUnitOnly."""
        content = root / 'content'
        for path, engines in layout.items():
            write_model(content, path, convert_model(tpf2_car(engines), path, []))
            icons = content / Path(path).parent / 'icons'
            icons.mkdir(exist_ok=True)
            stem = Path(path).stem
            for suffix in ('_icon_small@2x.tga', '_store.tga'):
                (icons / f'{stem}{suffix}').write_bytes(b'icon')
        # a group parent of a multiple unit: no compartment, must stay untouched
        placeholder = convert_model(tpf2_car([]), 'x.mdl', [])
        placeholder['metadata']['transportVehicle']['compartments'] = {}
        write_model(content, 'vehicle/train/unit_group.mdl', placeholder)
        return content

    def test_show_changes_nothing(self):
        with tempfile.TemporaryDirectory() as td:
            content = self.package(Path(td), {'vehicle/train/a.mdl': ['ELECTRIC'], 'vehicle/train/b.mdl': []})
            before = {p: p.read_bytes() for p in content.rglob('*') if p.is_file()}
            self.assertEqual(apply_single_vehicle_mode(Path(td), 'show'), [])
            self.assertEqual(before, {p: p.read_bytes() for p in content.rglob('*') if p.is_file()})

    def test_hide_empties_filter_tags_of_multiple_unit_parts_only(self):
        with tempfile.TemporaryDirectory() as td:
            content = self.package(Path(td), {'vehicle/train/a.mdl': ['ELECTRIC'], 'vehicle/train/b.mdl': []})
            apply_single_vehicle_mode(Path(td), 'hide')
            for name in ('a', 'b'):
                self.assertEqual(array(read_model(content, f'vehicle/train/{name}.mdl')['metadata']['transportVehicle']['filterTags']), [])
            group = read_model(content, 'vehicle/train/unit_group.mdl')['metadata']['transportVehicle']
            self.assertEqual(array(group['filterTags']), ['default'])

    def test_group_folds_the_whole_mod_into_one_row(self):
        with tempfile.TemporaryDirectory() as td:
            content = self.package(Path(td), {'vehicle/train/a.mdl': ['ELECTRIC'], 'vehicle/train/b.mdl': [],
                                              'vehicle/train/s1/c.mdl': [], 'vehicle/train/s1/d.mdl': ['ELECTRIC'],
                                              'vehicle/train/s2/lonely.mdl': []})
            diagnostics = apply_single_vehicle_mode(Path(td), 'group', 'テストMOD')
            self.assertEqual(sum(d['code'] == 'SINGLE_VEHICLE_GROUP_CREATED' for d in diagnostics), 1)
            group_path = 'vehicle/train/single_group_rail.mdl'
            group = read_model(content, group_path)
            transport = group['metadata']['transportVehicle']
            self.assertEqual(array(transport['filterTags']), [])
            self.assertEqual(array(transport['compartments']), [])
            self.assertEqual(group['metadata']['description']['name'], 'テストMOD 編成の部品（5両）')
            for member, expected in (('a', 'single_group_rail.mdl'), ('b', 'single_group_rail.mdl'),
                                     ('s1/c', '../single_group_rail.mdl'), ('s1/d', '../single_group_rail.mdl'),
                                     ('s2/lonely', '../single_group_rail.mdl')):
                tv = read_model(content, f'vehicle/train/{member}.mdl')['metadata']['transportVehicle']
                # TPF3 does not accept a literal ../ : a member in a deeper folder names the group from the content root
                self.assertEqual(tv['groupFileName'], expected if '/' not in member else '/' + group_path, member)
                self.assertEqual(array(tv['filterTags']), ['default'])
            icons = content / 'vehicle/train/icons'
            self.assertTrue((icons / 'single_group_rail_icon_small@2x.tga').is_file())
            self.assertTrue((icons / 'single_group_rail_store.tga').is_file())
            self.assertNotIn('groupFileName', read_model(content, 'vehicle/train/unit_group.mdl')['metadata']['transportVehicle'])

    def test_the_group_parent_of_a_multiple_unit_is_never_folded_into_a_group(self):
        """A unit's parent row that has real cars (compartments) must stay a parent, or the unit vanishes."""
        with tempfile.TemporaryDirectory() as td:
            content = self.package(Path(td), {'vehicle/train/a.mdl': ['ELECTRIC'], 'vehicle/train/b.mdl': [],
                                              'vehicle/train/parent.mdl': ['ELECTRIC']})
            unit = {'vehicles': table([{'name': 'a.mdl', 'forward': True}]), 'groupFileName': 'parent.mdl',
                    'name': 'ユニット', 'desc': 'x', 'filterTags': table(['default'])}
            (content / 'vehicle/train/unit.mu.lua').write_text(dumps(unit), 'utf-8')
            for mode in ('group', 'hide'):
                apply_single_vehicle_mode(Path(td), mode)
                parent = read_model(content, 'vehicle/train/parent.mdl')['metadata']['transportVehicle']
                self.assertNotIn('groupFileName', parent, mode)
            for name in ('a', 'b'):
                tv = read_model(content, f'vehicle/train/{name}.mdl')['metadata']['transportVehicle']
                self.assertEqual(tv['groupFileName'], 'single_group_rail.mdl')
            group = read_model(content, 'vehicle/train/single_group_rail.mdl')
            self.assertIn('2種類', group['metadata']['description']['description'])

    def test_a_real_car_that_is_the_group_parent_of_a_unit_is_listed_as_the_group_row_only(self):
        """MOD 9000000011 / 9000000007: the head car named as the unit's groupFileName showed up as a one-car vehicle."""
        for mode, expected in (('group', []), ('hide', []), ('show', ['default'])):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as td:
                content = self.package(Path(td), {'vehicle/train/a.mdl': ['ELECTRIC'], 'vehicle/train/parent.mdl': ['ELECTRIC']})
                unit = {'vehicles': table([{'name': 'a.mdl', 'forward': True}]), 'groupFileName': 'parent.mdl',
                        'name': 'ユニット', 'desc': 'x', 'filterTags': table(['default'])}
                (content / 'vehicle/train/unit.mu.lua').write_text(dumps(unit), 'utf-8')
                diagnostics = apply_single_vehicle_mode(Path(td), mode)
                parent = read_model(content, 'vehicle/train/parent.mdl')['metadata']['transportVehicle']
                self.assertEqual(array(parent['filterTags']), expected)
                self.assertEqual('GROUP_PARENT_CAR_ONLY_GROUP' in {d['code'] for d in diagnostics}, mode != 'show')

    def unit_with_parent_as_car(self, td):
        """MOD 9000000011 / 9000000007: the unit's groupFileName names a car that is also one of the unit's cars."""
        content = self.package(Path(td), {'vehicle/train/a.mdl': ['ELECTRIC'], 'vehicle/train/parent.mdl': ['ELECTRIC']})
        unit = {'vehicles': table([{'name': 'parent.mdl', 'forward': True}, {'name': 'a.mdl', 'forward': True}]),
                'groupFileName': 'parent.mdl', 'name': 'ユニット', 'desc': 'x', 'filterTags': table(['default'])}
        (content / 'vehicle/train/unit.mu.lua').write_text(dumps(unit), 'utf-8')
        return content

    def test_a_parent_that_is_also_a_car_of_its_unit_gets_a_stand_in_and_becomes_a_part(self):
        with tempfile.TemporaryDirectory() as td:
            content = self.unit_with_parent_as_car(td)
            diagnostics = apply_single_vehicle_mode(Path(td), 'group')
            self.assertIn('GROUP_PARENT_CAR_MOVED_TO_PARTS', {d['code'] for d in diagnostics})
            unit = parse((content / 'vehicle/train/unit.mu.lua').read_text('utf-8'))
            self.assertEqual(unit['groupFileName'], 'parent_group.mdl')
            self.assertEqual([entry['name'] for entry in array(unit['vehicles'])], ['parent.mdl', 'a.mdl'])
            stand_in = read_model(content, 'vehicle/train/parent_group.mdl')['metadata']['transportVehicle']
            self.assertEqual(array(stand_in['filterTags']), [])
            self.assertEqual(array(stand_in['compartments']), [])
            self.assertNotIn('groupFileName', stand_in)
            car = read_model(content, 'vehicle/train/parent.mdl')['metadata']['transportVehicle']
            self.assertEqual(array(car['filterTags']), ['default'])
            self.assertEqual(car['groupFileName'], 'single_group_rail.mdl')
            self.assertTrue((content / 'vehicle/train/icons/parent_group_store.tga').is_file())
            group = read_model(content, 'vehicle/train/single_group_rail.mdl')
            self.assertIn('2種類', group['metadata']['description']['description'])

    def test_a_stand_in_in_another_folder_is_named_from_the_content_root(self):
        with tempfile.TemporaryDirectory() as td:
            content = self.package(Path(td), {'vehicle/train/a.mdl': ['ELECTRIC'], 'vehicle/train/deep/parent.mdl': ['ELECTRIC'],
                                              'vehicle/train/deep/b.mdl': []})
            unit = {'vehicles': table([{'name': 'deep/parent.mdl', 'forward': True}]), 'groupFileName': 'deep/parent.mdl',
                    'name': 'ユニット', 'desc': 'x', 'filterTags': table(['default'])}
            (content / 'vehicle/train/unit.mu.lua').write_text(dumps(unit), 'utf-8')
            apply_single_vehicle_mode(Path(td), 'group')
            self.assertEqual(parse((content / 'vehicle/train/unit.mu.lua').read_text('utf-8'))['groupFileName'], 'deep/parent_group.mdl')
            self.assertTrue((content / 'vehicle/train/deep/parent_group.mdl').is_file())

    def test_a_model_that_names_the_parent_as_its_group_follows_it_to_the_stand_in(self):
        with tempfile.TemporaryDirectory() as td:
            content = self.unit_with_parent_as_car(td)
            variant = read_model(content, 'vehicle/train/a.mdl')
            variant['metadata']['transportVehicle']['groupFileName'] = 'parent.mdl'
            write_model(content, 'vehicle/train/a.mdl', variant)
            apply_single_vehicle_mode(Path(td), 'group')
            tv = read_model(content, 'vehicle/train/a.mdl')['metadata']['transportVehicle']
            self.assertEqual(tv['groupFileName'], 'parent_group.mdl')

    def test_hide_only_empties_the_filter_tags_of_a_parent_that_is_also_a_car(self):
        for mode in ('hide', 'show'):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as td:
                content = self.unit_with_parent_as_car(td)
                apply_single_vehicle_mode(Path(td), mode)
                car = read_model(content, 'vehicle/train/parent.mdl')['metadata']['transportVehicle']
                self.assertEqual(array(car['filterTags']), [] if mode == 'hide' else ['default'])
                self.assertFalse((content / 'vehicle/train/parent_group.mdl').exists())
                unit = parse((content / 'vehicle/train/unit.mu.lua').read_text('utf-8'))
                self.assertEqual(unit['groupFileName'], 'parent.mdl')

    def test_a_group_parent_that_is_sold_on_its_own_keeps_its_row(self):
        with tempfile.TemporaryDirectory() as td:
            content = self.package(Path(td), {'vehicle/train/a.mdl': ['ELECTRIC'], 'vehicle/train/parent.mdl': ['ELECTRIC']})
            parent = read_model(content, 'vehicle/train/parent.mdl')
            del parent['metadata']['transportVehicle']['multipleUnitOnly']
            write_model(content, 'vehicle/train/parent.mdl', parent)
            unit = {'vehicles': table([{'name': 'a.mdl', 'forward': True}]), 'groupFileName': 'parent.mdl',
                    'name': 'ユニット', 'desc': 'x', 'filterTags': table(['default'])}
            (content / 'vehicle/train/unit.mu.lua').write_text(dumps(unit), 'utf-8')
            apply_single_vehicle_mode(Path(td), 'group')
            tv = read_model(content, 'vehicle/train/parent.mdl')['metadata']['transportVehicle']
            self.assertEqual(array(tv['filterTags']), ['default'])

    def test_the_group_row_takes_the_name_and_icons_of_the_car_with_a_driver_seat(self):
        with tempfile.TemporaryDirectory() as td:
            content = self.package(Path(td), {'vehicle/train/a.mdl': [], 'vehicle/train/b.mdl': [], 'vehicle/train/c_cab.mdl': ['ELECTRIC']})
            cab = read_model(content, 'vehicle/train/c_cab.mdl')
            cab['metadata']['seatProvider'] = {'seats': table([{'crew': True, 'group': 'RootNode', 'transf': IDENTITY}])}
            write_model(content, 'vehicle/train/c_cab.mdl', cab)
            (content / 'vehicle/train/icons/c_cab_store.tga').write_bytes(b'cab')
            apply_single_vehicle_mode(Path(td), 'group')
            self.assertEqual((content / 'vehicle/train/icons/single_group_rail_store.tga').read_bytes(), b'cab')
            group = read_model(content, 'vehicle/train/single_group_rail.mdl')
            self.assertEqual(group['metadata']['landVehicle']['engines'][1]['type'], 'ELECTRIC')

    def test_a_part_of_units_without_any_icon_is_hidden_in_every_mode(self):
        for mode in ('group', 'show', 'hide'):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as td:
                content = self.package(Path(td), {'vehicle/train/used.mdl': [], 'vehicle/train/used_plain.mdl': [],
                                                  'vehicle/train/stray.mdl': [], 'vehicle/train/pic.mdl': []})
                for name in ('used_plain', 'stray'):
                    for suffix in ('_icon_small@2x.tga', '_store.tga'):
                        (content / f'vehicle/train/icons/{name}{suffix}').unlink()
                unit = {'vehicles': table([{'name': 'used.mdl', 'forward': True}, {'name': 'used_plain.mdl', 'forward': True}]),
                        'groupFileName': 'unit_group.mdl', 'name': 'ユニット', 'desc': 'x'}
                (content / 'vehicle/train/unit.mu.lua').write_text(dumps(unit), 'utf-8')
                diagnostics = apply_single_vehicle_mode(Path(td), mode)
                self.assertIn('PART_WITHOUT_IMAGE_HIDDEN', [d['code'] for d in diagnostics])
                tags = lambda name: array(read_model(content, f'vehicle/train/{name}.mdl')['metadata']['transportVehicle']['filterTags'])
                for name in ('stray', 'used_plain'):
                    self.assertEqual(tags(name), [], f'{name}: no picture, even when a unit uses it')
                    self.assertNotIn('groupFileName', read_model(content, f'vehicle/train/{name}.mdl')['metadata']['transportVehicle'])
                if mode != 'hide':
                    self.assertEqual(tags('used'), ['default'])
                    self.assertEqual(tags('pic'), ['default'])

    def test_a_car_sold_on_its_own_stays_even_without_an_icon(self):
        with tempfile.TemporaryDirectory() as td:
            content = self.package(Path(td), {'vehicle/train/solo.mdl': [], 'vehicle/train/part.mdl': []})
            data = read_model(content, 'vehicle/train/solo.mdl')
            data['metadata']['transportVehicle'].pop('multipleUnitOnly', None)
            write_model(content, 'vehicle/train/solo.mdl', data)
            for name in ('solo', 'part'):
                for suffix in ('_icon_small@2x.tga', '_store.tga'):
                    (content / f'vehicle/train/icons/{name}{suffix}').unlink()
            apply_single_vehicle_mode(Path(td), 'show')
            self.assertEqual(array(read_model(content, 'vehicle/train/solo.mdl')['metadata']['transportVehicle']['filterTags']), ['default'])
            self.assertEqual(array(read_model(content, 'vehicle/train/part.mdl')['metadata']['transportVehicle']['filterTags']), [])

    def test_cars_sold_on_their_own_and_parts_of_units_get_separate_rows(self):
        with tempfile.TemporaryDirectory() as td:
            content = self.package(Path(td), {'vehicle/train/p1.mdl': [], 'vehicle/train/p2.mdl': [],
                                              'vehicle/train/s1.mdl': [], 'vehicle/train/s2.mdl': [], 'vehicle/train/s3.mdl': []})
            for name in ('s1', 's2', 's3'):
                data = read_model(content, f'vehicle/train/{name}.mdl')
                data['metadata']['transportVehicle'].pop('multipleUnitOnly', None)
                write_model(content, f'vehicle/train/{name}.mdl', data)
            diagnostics = apply_single_vehicle_mode(Path(td), 'group', 'M')
            self.assertEqual(sum(d['code'] == 'SINGLE_VEHICLE_GROUP_CREATED' for d in diagnostics), 2)
            parts = read_model(content, 'vehicle/train/single_group_rail.mdl')['metadata']['description']['name']
            solo = read_model(content, 'vehicle/train/single_group_rail_solo.mdl')['metadata']['description']['name']
            self.assertEqual(parts, 'M 編成の部品（2両）')
            self.assertEqual(solo, 'M 単行車両（3両）')
            for name, group in (('p1', 'single_group_rail.mdl'), ('p2', 'single_group_rail.mdl'),
                                ('s1', 'single_group_rail_solo.mdl'), ('s3', 'single_group_rail_solo.mdl')):
                self.assertEqual(read_model(content, f'vehicle/train/{name}.mdl')['metadata']['transportVehicle']['groupFileName'], group)

    def test_a_lone_standalone_car_stays_in_the_list_on_its_own(self):
        with tempfile.TemporaryDirectory() as td:
            content = self.package(Path(td), {'vehicle/train/p1.mdl': [], 'vehicle/train/p2.mdl': [], 'vehicle/train/s1.mdl': []})
            data = read_model(content, 'vehicle/train/s1.mdl')
            data['metadata']['transportVehicle'].pop('multipleUnitOnly', None)
            write_model(content, 'vehicle/train/s1.mdl', data)
            apply_single_vehicle_mode(Path(td), 'group')
            self.assertNotIn('groupFileName', read_model(content, 'vehicle/train/s1.mdl')['metadata']['transportVehicle'])
            self.assertFalse((content / 'vehicle/train/single_group_rail_solo.mdl').exists())

    def test_group_rows_are_made_per_carrier(self):
        with tempfile.TemporaryDirectory() as td:
            content = self.package(Path(td), {'vehicle/train/a.mdl': [], 'vehicle/train/b.mdl': [], 'vehicle/road/c.mdl': [], 'vehicle/road/d.mdl': []})
            for name in ('c', 'd'):
                data = read_model(content, f'vehicle/road/{name}.mdl')
                data['metadata']['transportVehicle']['carrier'] = 'ROAD'
                write_model(content, f'vehicle/road/{name}.mdl', data)
            apply_single_vehicle_mode(Path(td), 'group', 'M')
            rail = read_model(content, 'vehicle/train/single_group_rail.mdl')
            road = read_model(content, 'vehicle/road/single_group_road.mdl')
            self.assertIn('編成の部品・RAIL', rail['metadata']['description']['name'])
            self.assertIn('編成の部品・ROAD', road['metadata']['description']['name'])

    def test_group_without_two_vehicles_in_a_folder_says_so(self):
        with tempfile.TemporaryDirectory() as td:
            self.package(Path(td), {'vehicle/train/a.mdl': []})
            codes = [d['code'] for d in apply_single_vehicle_mode(Path(td), 'group')]
            self.assertEqual(codes, ['SINGLE_VEHICLE_GROUP_NONE'])

    def test_hidden_vehicles_are_not_folded_and_vehicle_with_own_group_is_kept(self):
        with tempfile.TemporaryDirectory() as td:
            content = self.package(Path(td), {'vehicle/train/a.mdl': [], 'vehicle/train/b.mdl': [], 'vehicle/train/c.mdl': []})
            for name, edit in (('a', lambda tv: tv.update(filterTags={})), ('b', lambda tv: tv.update(groupFileName='mine.mdl'))):
                data = read_model(content, f'vehicle/train/{name}.mdl')
                edit(data['metadata']['transportVehicle'])
                write_model(content, f'vehicle/train/{name}.mdl', data)
            self.assertEqual([d['code'] for d in apply_single_vehicle_mode(Path(td), 'group')], ['SINGLE_VEHICLE_GROUP_NONE'])
            self.assertEqual(read_model(content, 'vehicle/train/b.mdl')['metadata']['transportVehicle']['groupFileName'], 'mine.mdl')

    def test_placeholder_is_geometry_free_and_inherits_engine_data(self):
        first = convert_model(tpf2_car(['ELECTRIC']), 'a.mdl', [])
        placeholder = group_placeholder('グループ', '説明', [first])
        self.assertEqual(placeholder['version'], 2)
        self.assertNotIn('children', placeholder['lods'][1]['node'])
        self.assertEqual(array(placeholder['metadata']['transportVehicle']['engineTransportModes']), ['ELECTRIC_TRAIN'])
        self.assertEqual(placeholder['metadata']['landVehicle']['engines'][1]['type'], 'ELECTRIC')

    def test_unknown_mode_is_rejected_before_any_work(self):
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(ValueError):
                apply_single_vehicle_mode(Path(td), 'everything')
            with self.assertRaises(ValueError):
                pipeline.convert(Path(td), Path(td) / 'out', single_vehicles='everything')


if __name__ == '__main__':
    unittest.main()
