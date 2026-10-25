from copy import deepcopy
from pathlib import Path
import tempfile
import unittest

from converter.adapters.tpf3_writer import convert_model
from converter.parsers.lua import dumps, parse
from converter.resolver import array, table
from converter.transforms.singles import apply_single_vehicle_mode
from tests.test_singles import tpf2_car


class GroupParentPurchaseTests(unittest.TestCase):
    parent = 'res/models/model/vehicle/train/group_shinkansen_800.mdl'
    car = 'res/models/model/vehicle/train/shinkansen_800_car_1.mdl'
    unit = 'res/config/multiple_unit/Shinkansen_800.lua'

    def inputs(self):
        model = tpf2_car(['ELECTRIC'])
        model['metadata']['description'] = {'name': 'Shinkansen 800', 'description': 'Menu'}
        parsed = {self.parent: deepcopy(model), self.car: deepcopy(model),
                  self.unit: {'vehicles': table([{'name': 'vehicle/train/shinkansen_800_car_1.mdl', 'forward': True}]),
                              'groupFileName': 'vehicle/train/group_shinkansen_800.mdl'}}
        mapping = {self.parent: 'vehicle/train/group_shinkansen_800.mdl',
                   self.car: 'vehicle/train/shinkansen_800_car_1.mdl',
                   self.unit: 'vehicle/train/9000000004/shinkansen_800.mu.lua'}
        return model, parsed, mapping

    def converted(self, model, parsed, mapping):
        diagnostics = []
        output = convert_model(deepcopy(model), self.parent, diagnostics,
                               files=parsed, parsed=parsed, mapping=mapping)
        return output, diagnostics

    def test_menu_only_parent_is_not_a_car_but_keeps_its_model_and_group_role(self):
        model, parsed, mapping = self.inputs()
        visible = deepcopy(model)
        visible['metadata']['transportVehicle']['multipleUnitOnly'] = False
        before, _ = self.converted(visible, parsed, mapping)
        output, diagnostics = self.converted(model, parsed, mapping)
        transport = output['metadata']['transportVehicle']
        self.assertEqual(array(transport['filterTags']), [])
        self.assertNotIn('groupFileName', transport)
        self.assertEqual(output['lods'], before['lods'])
        self.assertEqual(transport['compartments'], before['metadata']['transportVehicle']['compartments'])
        self.assertEqual(output['metadata']['landVehicle'], before['metadata']['landVehicle'])
        self.assertIn('MENU_GROUP_NOT_PURCHASABLE', {d['code'] for d in diagnostics})
        car = convert_model(parsed[self.car], self.car, [], files=parsed, parsed=parsed, mapping=mapping)
        self.assertEqual(array(car['metadata']['transportVehicle']['filterTags']), ['default'])
        for mode in ('show', 'hide', 'group'):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as td:
                target = Path(td) / 'content' / mapping[self.parent]
                target.parent.mkdir(parents=True)
                target.write_text(dumps(output), 'utf-8')
                mu = target.with_name('unit.mu.lua')
                mu.write_text(dumps({'vehicles': table([{'name': 'car.mdl', 'forward': True}]),
                                     'groupFileName': target.name, 'filterTags': table(['default'])}), 'utf-8')
                apply_single_vehicle_mode(Path(td), mode)
                self.assertEqual(parse(target.read_text('utf-8')), output)

    def test_parent_used_as_actual_consist_car_is_still_available(self):
        model, parsed, mapping = self.inputs()
        parsed[self.unit]['vehicles'][2] = {'name': 'vehicle/train/group_shinkansen_800.mdl', 'forward': False}
        output, diagnostics = self.converted(model, parsed, mapping)
        self.assertEqual(array(output['metadata']['transportVehicle']['filterTags']), ['default'])
        self.assertNotIn('MENU_GROUP_NOT_PURCHASABLE', {d['code'] for d in diagnostics})

    def test_purchasable_parent_and_unreferenced_multiple_unit_car_are_unchanged(self):
        for menu_flag, has_group in ((False, True), (True, False)):
            with self.subTest(menu_flag=menu_flag, has_group=has_group):
                model, parsed, mapping = self.inputs()
                model['metadata']['transportVehicle']['multipleUnitOnly'] = menu_flag
                if not has_group:
                    parsed[self.unit].pop('groupFileName')
                output, diagnostics = self.converted(model, parsed, mapping)
                self.assertEqual(array(output['metadata']['transportVehicle']['filterTags']), ['default'])
                self.assertNotIn('MENU_GROUP_NOT_PURCHASABLE', {d['code'] for d in diagnostics})


if __name__ == '__main__':
    unittest.main()
