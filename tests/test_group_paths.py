from copy import deepcopy
from pathlib import Path
import tempfile
import unittest

from converter.parsers.lua import dumps, parse
from converter.pipeline import convert
from converter.resolver import array
from tests.test_conversion import fixture


class GroupPathTests(unittest.TestCase):
    def test_cross_folder_series_groups_resolve_without_parent_traversal(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)/'mod'
            original = fixture(root)
            original['metadata']['railVehicle']['engines'] = {1: {'type': 'ELECTRIC'}}
            unit_path = root/'res/models/model/vehicle/train/unit.mdl'
            # The parent and its parts are in sibling folders, like E3/E5.
            unit_path.unlink()
            for series in ('e3_k', 'e3_t'):
                group_name = f'vehicle/train/{series}_menu/group.mdl'
                part = deepcopy(original)
                part['metadata']['transportVehicle']['groupFileName'] = group_name
                for name in ('first', 'second'):
                    target = root/f'res/models/model/vehicle/train/{series}/{name}.mdl'
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text(dumps(part), 'utf-8')
                group = deepcopy(original)
                group['metadata']['transportVehicle']['groupFileName'] = group_name
                group['metadata']['transportVehicle']['multipleUnitOnly'] = True
                target = root/'res/models/model'/group_name
                target.parent.mkdir(parents=True)
                target.write_text(dumps(group), 'utf-8')
                mu = root/f'res/config/multiple_unit/{series}.lua'
                mu.parent.mkdir(parents=True, exist_ok=True)
                mu.write_text(dumps({'vehicles': {1: {'name': f'vehicle/train/{series}/first.mdl', 'forward': True}},
                                    'groupFileName': group_name, 'name': series}), 'utf-8')
            for mode in ('show', 'group', 'hide'):
                result = convert(root, Path(td)/mode, single_vehicles=mode, siblings=None)
                self.assertEqual(result['status'], 'success', result['diagnostics'])
                content = Path(result['package_dir'])/'content'
                for series in ('e3_k', 'e3_t'):
                    expected = f'/vehicle/train/{series}_menu/group.mdl'
                    for name in ('first', 'second'):
                        model = parse((content/f'vehicle/train/{series}/{name}.mdl').read_text('utf-8'))
                        self.assertEqual(model['metadata']['transportVehicle']['groupFileName'], expected)
                        self.assertTrue((content/expected.lstrip('/')).is_file())
                    parent = parse((content/expected.lstrip('/')).read_text('utf-8'))['metadata']['transportVehicle']
                    self.assertNotIn('groupFileName', parent)
                    self.assertEqual(array(parent['filterTags']), [])
                self.assertEqual(len(list(content.rglob('*.mu.lua'))), 2)
                self.assertFalse(list(content.rglob('single_group_*.mdl')))
                self.assertNotIn('VEHICLE_GROUP_OUTSIDE_FOLDER', {d['code'] for d in result['diagnostics']})

    def test_same_folder_parent_keeps_existing_rewritten_path(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)/'mod'
            original = fixture(root)
            original['metadata']['transportVehicle']['groupFileName'] = 'vehicle/train/group.mdl'
            (root/'res/models/model/vehicle/train/unit.mdl').write_text(dumps(original), 'utf-8')
            parent = deepcopy(original)
            (root/'res/models/model/vehicle/train/group.mdl').write_text(dumps(parent), 'utf-8')
            result = convert(root, Path(td)/'out', siblings=None)
            self.assertEqual(result['status'], 'success', result['diagnostics'])
            content = Path(result['package_dir'])/'content/vehicle/train'
            self.assertEqual(parse((content/'unit.mdl').read_text('utf-8'))['metadata']['transportVehicle']['groupFileName'], '/vehicle/train/group.mdl')
            self.assertNotIn('groupFileName', parse((content/'group.mdl').read_text('utf-8'))['metadata']['transportVehicle'])
