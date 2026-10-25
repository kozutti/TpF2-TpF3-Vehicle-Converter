from __future__ import annotations
import json
import tempfile
import threading
import unittest
import zipfile
import struct
from copy import deepcopy
from pathlib import Path
from converter.adapters.tpf3_writer import convert_model, convert_material, nodes
from converter.parsers.lua import parse, dumps
from converter.pipeline import convert, analyze, plan, validate, input_root
from converter.resolver import array, table

IDENTITY = table([1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1])

def fixture(root, dynamic=False):
    root.mkdir(parents=True)
    (root / 'mod.lua').write_text('function data() return {info={name="Unit",authors={{name="Author",role="CREATOR"}}},runFn=function() ' + ('error("must not run")' if dynamic else '') + ' end} end', encoding='utf-8')
    mesh = 'vehicle/train/unit/wheel.msh'
    door = {'name': 'door', 'transf': IDENTITY, 'animations': {
        'open_doors_left': {'type': 'FILE_REF', 'params': {'id': 'vehicle/train/unit/open.ani'}},
        'close_doors_left': {'type': 'FILE_REF', 'forward': False, 'params': {'id': 'vehicle/train/unit/open.ani'}}}}
    bogie = {'name': 'bogie', 'transf': table([1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 6, 0, .7, 1]),
             'children': {1: {'name': 'wheel', 'mesh': mesh, 'materials': {1: 'vehicle/train/unit/plain.mtl'}, 'transf': IDENTITY}}}
    rear_bogie = deepcopy(bogie)
    rear_bogie['transf'][13] = -6
    model = {'version': 1, 'lods': {1: {'node': {'name': 'RootNode', 'transf': IDENTITY,
                        'children': {1: door, 2: bogie, 3: rear_bogie}}, 'visibleFrom': 0, 'visibleTo': 1000}},
             'metadata': {'railVehicle': {'configs': {1: {'axles': {1: mesh}}}, 'engines': {}, 'weight': 20, 'topSpeed': 30},
              'transportVehicle': {'carrier': 'RAIL', 'loadSpeed': 4, 'reversible': True,
                  'compartmentsList': {1: {'loadConfigs': {1: {'cargoEntries': {1: {'type': 'PASSENGERS', 'capacity': 60, 'seats': {}}}, 'toHide': {}}}}}},
               'seatProvider': {'seats': {1: {'group': 1, 'transf': IDENTITY}}}}}
    p = root / 'res/models/model/vehicle/train/unit.mdl'
    p.parent.mkdir(parents=True)
    p.write_text(dumps(model), encoding='utf-8')
    p = root / 'res/models/animation/vehicle/train/unit/open.ani'
    p.parent.mkdir(parents=True)
    p.write_text(dumps({'times': {1: 0, 2: 3100}, 'transfs': {1: IDENTITY, 2: IDENTITY}}), encoding='utf-8')
    p = root / 'res/models/mesh' / mesh
    p.parent.mkdir(parents=True)
    p.write_text(dumps({'vertexAttr': {'position': {'offset': 0, 'count': 36, 'numComp': 3}},
                  'subMeshes': {1: {'indices': {'position': {'offset': 36, 'count': 12}}}}}), encoding='utf-8')
    Path(str(p) + '.blob').write_bytes(struct.pack('<9f3I', 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 1, 2))
    p = root / 'res/models/material/vehicle/train/unit/plain.mtl'
    p.parent.mkdir(parents=True)
    p.write_text(dumps({'type': 'PHYSICAL', 'params': {'albedo_scale': {'albedoScale': {1: 1, 2: 1, 3: 1}}}}), encoding='utf-8')
    return model

class ConversionTests(unittest.TestCase):
    def test_anchors_and_fake_bogies_keep_zero_based_node_identity(self):
        with tempfile.TemporaryDirectory() as td:
            original = fixture(Path(td) / 'mod')
            metadata = original['metadata']
            # The bogie has a nonzero offset: attaching to an adjacent node moves the seat.
            metadata['seatProvider']['seats'] = {1: {'group': 2, 'transf': IDENTITY}, 2: {'group': 0, 'transf': IDENTITY}}
            metadata['railVehicle']['configs'][1]['fakeBogies'] = {1: {'group': 2, 'position': 6, 'offset': 0, 'upright': False}}
            diagnostics = []
            converted = convert_model(original, 'unit.mdl', diagnostics)
            flat = list(nodes(converted['lods'][1]['node']))
            seats = converted['metadata']['seatProvider']['seats']
            self.assertEqual(seats[1]['group'], flat[2][0]['name'])
            self.assertEqual(seats[2]['group'], flat[0][0]['name'])
            self.assertEqual(flat[2][0]['transf'][13], 6)
            fake = converted['metadata']['railVehicle']['config']['fakeBogies'][1][1]
            self.assertEqual(fake['group'], seats[1]['group'])
            self.assertFalse(fake['upright'])
            self.assertEqual(fake['position'], 6)

    def test_embedded_doors_and_unknown_event_diagnostics(self):
        with tempfile.TemporaryDirectory() as td:
            original = fixture(Path(td) / 'mod')
            door = original['lods'][1]['node']['children'][1]
            door['animations'] = {'open_all_doors': {'type': 'KEYFRAME_MATRIX', 'params': {'keyframes': {
                1: {'time': 0, 'transf': IDENTITY}, 2: {'time': 4200, 'transf': IDENTITY}}}},
                'custom_on': {'type': 'KEYFRAME_MATRIX', 'params': {'keyframes': {1: {'time': 0, 'transf': IDENTITY}}}},
                'empty_on': {'type': 'KEYFRAME_MATRIX', 'params': {'keyframes': {}}}}
            diagnostics = []
            converted = convert_model(original, 'unit.mdl', diagnostics)
            self.assertEqual(converted['metadata']['transportVehicle']['arrivalDelay'], 4200)
            self.assertIn('ANIMATION_TRIGGER_UNMAPPED', {d['code'] for d in diagnostics})
            self.assertIn('ANIMATION_EMPTY_DROPPED', {d['code'] for d in diagnostics})

    def test_fade_uses_vertex_shader_properties(self):
        material = convert_material({'type': 'PHYSICAL', 'params': {'fade_out_range': {'fadeOutEndDist': 1000}}}, 'm.mtl', [])
        self.assertEqual(material['params']['fade_out_range'], {'vertexProperties': {1: {'fadeOutEndDist': 1000}}})

    def test_door_timing_hierarchy_units_and_original_unchanged(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / '入力 MOD'
            original = fixture(root)
            snapshots = {p.relative_to(root): p.read_bytes() for p in root.rglob('*') if p.is_file()}
            analysis = analyze(root)
            result = convert(root, Path(td) / 'out')
            self.assertEqual(result['status'], 'success', result['diagnostics'])
            package = Path(result['package_dir'])
            converted = parse((package / 'content/vehicle/train/unit.mdl').read_text('utf-8'))
            self.assertEqual(converted['metadata']['transportVehicle']['arrivalDelay'], 3100)
            self.assertEqual(converted['metadata']['transportVehicle']['departureDelay'], 3100)
            self.assertEqual(converted['metadata']['landVehicle']['weightEmpty'], 20000)
            original_nodes = original['lods'][1]['node']
            target_nodes = converted['lods'][1]['node']
            self.assertFalse(target_nodes['children'][1]['animations']['close_doors_left']['forward'])
            self.assertEqual(original_nodes['transf'], target_nodes['transf'])
            self.assertEqual(original_nodes['children'][2]['transf'], target_nodes['children'][2]['transf'])
            self.assertEqual(original_nodes['children'][2]['children'][1]['transf'], target_nodes['children'][2]['children'][1]['transf'])
            for p, data in snapshots.items():
                self.assertEqual((root / p).read_bytes(), data)
            self.assertTrue(validate(package)['valid'])
            manifest = json.loads((Path(result['run_dir']) / 'reports/manifest.json').read_text('utf8'))
            self.assertEqual(len(manifest['input']), len(snapshots))

    def test_multiple_unit_uses_relative_names_and_depot_tags(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / 'mod'
            fixture(root)
            sub = root / 'res/models/model/vehicle/train/sub/other.mdl'
            sub.parent.mkdir(parents=True)
            sub.write_bytes((root / 'res/models/model/vehicle/train/unit.mdl').read_bytes())
            mu = root / 'res/config/multiple_unit/Pair.lua'
            mu.parent.mkdir(parents=True)
            mu.write_text('function data() return {vehicles={{name="vehicle/train/unit.mdl",forward=true},'
                          '{name="vehicle/train/sub/other.mdl",forward=false}},groupFileName="vehicle/train/unit.mdl",'
                          'name="Pair",desc="Pair"} end', encoding='utf-8')
            result = convert(root, Path(td) / 'out')
            self.assertEqual(result['status'], 'success', result['diagnostics'])
            content = Path(result['package_dir']) / 'content'
            unit = parse((content / 'vehicle/train/pair.mu.lua').read_text('utf-8'))
            self.assertEqual([v['name'] for v in array(unit['vehicles'])], ['unit.mdl', 'sub/other.mdl'])
            self.assertEqual(unit['groupFileName'], 'unit.mdl')
            self.assertEqual(array(unit['filterTags']), ['default'])
            transport = parse((content / 'vehicle/train/unit.mdl').read_text('utf-8'))['metadata']['transportVehicle']
            self.assertEqual(array(transport['transportModes']), array(transport['tags']))
            self.assertEqual(array(transport['engineTransportModes']), array(transport['engineTags']))
            self.assertEqual(transport['compartmentsList'], transport['compartments'])
            self.assertEqual(array(transport['filterTags']), ['default'])

    def test_icons_move_next_to_model_and_store_image_is_generated(self):
        def tga(width, height, bpp=32, kind=2):
            return bytes([0, 0, kind]) + bytes(9) + struct.pack('<HH', width, height) + bytes([bpp, 0]) + bytes([200, 100, 50, 255][:bpp // 8]) * (width * height)
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / 'mod'
            fixture(root)
            ui = root / 'res/textures/ui'
            for folder, size in (('models_20', (116, 40)), ('models_small', (320, 112))):
                (ui / folder / 'vehicle/train').mkdir(parents=True)
                (ui / folder / 'vehicle/train/Unit@2x.tga').write_bytes(tga(*size))
                (ui / folder / 'vehicle/train/Unit_cblend@2x.tga').write_bytes(tga(*size, 8, 3))
            (ui / 'models_20/vehicle/train/Orphan@2x.tga').write_bytes(tga(116, 40))
            result = convert(root, Path(td) / 'out')
            self.assertEqual(result['status'], 'success', result['diagnostics'])
            icons = Path(result['package_dir']) / 'content/vehicle/train/icons'
            self.assertEqual(sorted(p.name for p in icons.iterdir()), [
                'unit_icon20@2x.tga', 'unit_icon20_cblend@2x.tga',
                'unit_icon_small@2x.tga', 'unit_icon_small_cblend@2x.tga', 'unit_store.tga'])
            store = (icons / 'unit_store.tga').read_bytes()
            self.assertEqual(struct.unpack('<HH', store[12:16]), (453, 224))
            self.assertEqual(len(store), 18 + 453 * 224 * 4)
            # Letterboxed: top row transparent, middle row carries the icon colour.
            self.assertEqual(store[18 + 3], 0)
            middle = 18 + (112 * 453 + 226) * 4
            self.assertEqual(list(store[middle:middle + 4]), [200, 100, 50, 255])
            self.assertIn('ICON_MODEL_NOT_FOUND', {d['code'] for d in result['diagnostics']})
            self.assertFalse(any('textures/ui' in p.as_posix() for p in Path(result['package_dir']).rglob('*')))
            self.assertTrue(validate(Path(result['package_dir']))['valid'])

    def test_group_placeholder_inherits_member_engine_tags(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / 'mod'
            original = fixture(root)
            original['metadata']['railVehicle']['engines'] = {1: {'type': 'ELECTRIC', 'power': 0, 'tractiveEffort': 0}}
            (root / 'res/models/model/vehicle/train/unit.mdl').write_text(dumps(original), encoding='utf-8')
            group = deepcopy(original)
            group['metadata']['railVehicle'] = {'configs': {}, 'engines': {}, 'weight': 0, 'topSpeed': 0}
            group['metadata']['transportVehicle']['multipleUnitOnly'] = True
            (root / 'res/models/model/vehicle/train/group.mdl').write_text(dumps(group), encoding='utf-8')
            mu = root / 'res/config/multiple_unit/Solo.lua'
            mu.parent.mkdir(parents=True)
            mu.write_text('function data() return {vehicles={{name="vehicle/train/unit.mdl",forward=true}},'
                          'groupFileName="vehicle/train/group.mdl",name="Solo",desc="Solo"} end', encoding='utf-8')
            result = convert(root, Path(td) / 'out')
            content = Path(result['package_dir']) / 'content'
            transport = parse((content / 'vehicle/train/group.mdl').read_text('utf-8'))['metadata']['transportVehicle']
            self.assertEqual(array(transport['tags']), ['TRAIN', 'ELECTRIC_TRAIN'])
            self.assertEqual(array(transport['engineTags']), ['ELECTRIC_TRAIN'])

    def test_dynamic_mod_script_is_not_executed_and_only_warned_about(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / 'mod'
            fixture(root, dynamic=True)
            conversion_plan = plan(root)
            self.assertEqual(conversion_plan['status'], 'planned')
            self.assertIn(('MOD_SCRIPT_UNSUPPORTED', 'warning'),
                          {(d['code'], d['severity']) for d in conversion_plan['diagnostics']})
            result = convert(root, Path(td) / 'out')
            self.assertEqual(result['status'], 'success')
            self.assertTrue(validate(Path(result['package_dir']))['valid'])

    def test_cancelled_never_leaves_package(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / 'mod'
            fixture(root)
            event = threading.Event()
            event.set()
            result = convert(root, Path(td) / 'out', cancel=event)
            self.assertEqual(result['status'], 'cancelled')
            self.assertIsNone(result['package_dir'])
            self.assertFalse((Path(result['run_dir']) / '_work').exists())

    def test_zip_traversal_device_and_collision_rejected(self):
        for name in ['../escaped', '/absolute', 'C:/outside', 'CON.txt', 'a/../b', 'foo?.txt', 'a/./b']:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as td:
                p = Path(td) / 'evil.zip'
                with zipfile.ZipFile(p, 'w') as z:
                    z.writestr(name, 'data')
                with self.assertRaises(ValueError), input_root(p):
                    pass

    def test_repeat_run_has_same_id_without_overwriting(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / 'mod'
            fixture(root)
            r1, r2 = convert(root, Path(td) / 'out'), convert(root, Path(td) / 'out')
            self.assertNotEqual(r1['run_dir'], r2['run_dir'])
            self.assertEqual(Path(r1['package_dir']).name, Path(r2['package_dir']).name)


if __name__ == '__main__':
    unittest.main()
