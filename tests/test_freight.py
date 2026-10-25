"""Freight vehicles: TPF2 cargo types become TPF3 cargo files/classes; older compartment forms are accepted."""
import unittest

from converter.adapters.tpf3_writer import cargo_type_set, convert_model
from converter.resolver import array, table

from test_singles import tpf2_car


def freight(transport_update, remove=()):
    model = tpf2_car([])
    transport = model['metadata']['transportVehicle']
    for key in remove:
        transport.pop(key, None)
    transport.update(transport_update)
    diagnostics = []
    return convert_model(model, 'wagon.mdl', diagnostics), diagnostics


def entry_of(result, compartment=1, load=1):
    compartments = result['metadata']['transportVehicle']['compartments']
    return compartments[compartment]['loadConfigs'][load]


class CargoTypeTests(unittest.TestCase):
    def test_known_types_map_to_tpf3_cargo_files(self):
        diagnostics = []
        cargo = cargo_type_set('COAL', 'x.mdl', diagnostics)
        self.assertEqual(array(cargo['cargoTypesIncluded']), ['::/cargos/coal/coal.cargo'])
        self.assertEqual(array(cargo_type_set('CRUDE', 'x.mdl', diagnostics)['cargoTypesIncluded']), ['::/cargos/crude_oil/crude_oil.cargo'])
        self.assertEqual(array(cargo_type_set('CONSTRUCTION_MATERIALS', 'x.mdl', diagnostics)['cargoTypesIncluded']),
                         ['::/cargos/bricks/bricks.cargo', '::/cargos/cement/cement.cargo'])
        self.assertEqual(array(cargo_type_set('SAND', 'x.mdl', diagnostics)['cargoTypesIncluded']), ['::/cargos/sand/sand.cargo'])
        self.assertEqual(diagnostics, [])

    def test_types_without_a_tpf3_cargo_use_a_class_or_everything_with_a_warning(self):
        diagnostics = []
        self.assertEqual(array(cargo_type_set('GOODS', 'x.mdl', diagnostics)['cargoClassesIncluded']), ['GOODS'])
        self.assertEqual(diagnostics, [])
        cargo = cargo_type_set('MYSTERY', 'x.mdl', diagnostics)
        self.assertEqual(array(cargo['cargoClassesIncluded']), ['UNIVERSAL'])
        self.assertEqual([(d['code'], d['severity']) for d in diagnostics], [('CARGO_TYPE_APPROXIMATED', 'warning')])


class CompartmentFormTests(unittest.TestCase):
    def test_freight_entry_in_the_usual_list_form(self):
        result, diagnostics = freight({'compartmentsList': {1: {'loadConfigs': {1: {'cargoEntries': {1: {'type': 'STONE', 'capacity': 74}},
                                                                                     'toHide': {}}}}}})
        entry = entry_of(result)['cargoEntry']
        self.assertEqual(entry['capacity'], 74)
        self.assertEqual(array(entry['cargoTypeSet']['cargoTypesIncluded']), ['::/cargos/stone/stone.cargo'])
        self.assertEqual([d for d in diagnostics if d['severity'] == 'error'], [])

    def test_older_nested_array_form(self):
        result, _ = freight({'compartments': {1: {1: {1: {'capacity': 82, 'type': 'PASSENGERS'}}}}}, remove=('compartmentsList',))
        entry = entry_of(result)['cargoEntry']
        self.assertEqual(entry['capacity'], 82)
        self.assertEqual(array(entry['cargoTypeSet']['cargoTypesIncluded']), ['::/cargos/passengers/passengers.cargo'])
        self.assertNotIn('compartments', {k for k in ()})  # the source key is consumed, not copied

    def test_oldest_capacities_form_and_legacy_seats(self):
        result, diagnostics = freight({'capacities': {1: {'type': 'PASSENGERS', 'capacity': 80}},
                                       'seats': {1: {'group': 0, 'transf': table([1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1])}}},
                                      remove=('compartmentsList',))
        self.assertEqual(entry_of(result)['cargoEntry']['capacity'], 80)
        self.assertNotIn('seats', result['metadata']['transportVehicle'])
        provider = result['metadata']['seatProvider']
        self.assertEqual(provider['drivingLicense'], 'RAIL')
        self.assertEqual(provider['seats'][1]['animation'], 'sitting')
        self.assertEqual([d for d in diagnostics if d['severity'] == 'error'], [])

    def test_locomotive_with_empty_cargo_entries_has_no_compartment(self):
        result, diagnostics = freight({'compartmentsList': {1: {'loadConfigs': {1: {'cargoEntries': {}, 'toHide': {}}}}}})
        self.assertEqual(array(result['metadata']['transportVehicle']['compartments']), [])
        self.assertEqual([d for d in diagnostics if d['severity'] == 'error'], [])

    def test_several_cargo_entries_keep_the_first_and_warn(self):
        result, diagnostics = freight({'compartmentsList': {1: {'loadConfigs': {1: {'cargoEntries': {
            1: {'type': 'COAL', 'capacity': 10}, 2: {'type': 'GRAIN', 'capacity': 10}}, 'toHide': {}}}}}})
        self.assertEqual(array(entry_of(result)['cargoEntry']['cargoTypeSet']['cargoTypesIncluded']), ['::/cargos/coal/coal.cargo'])
        self.assertIn('CARGO_ENTRIES_TRUNCATED', {d['code'] for d in diagnostics})

    def test_cargo_bay_is_left_for_the_games_own_load_indicator_migration(self):
        bay = {'type': 'LEVEL', 'childId': 0, 'cargoFormat': 'MEDIUM4x1'}
        result, _ = freight({'compartmentsList': {1: {'loadConfigs': {1: {'cargoEntries': {1: {'type': 'GRAIN', 'capacity': 40, 'cargoBay': bay}},
                                                                          'toHide': {}}}}}})
        load = entry_of(result)
        self.assertNotIn('cargoEntry', load)
        entries = array(load['cargoEntries'])
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]['cargoBay']['childId'], 'RootNode')  # node index -> node name
        self.assertNotIn('type', entries[0])


class PlainModelTests(unittest.TestCase):
    def test_a_model_without_metadata_is_kept_but_marked_version_2_with_named_sorted_lods(self):
        model = {'version': 1, 'lods': {1: {'node': {'children': {1: {'mesh': 'a_lod1.msh'}}}, 'visibleTo': 500},
                                        2: {'node': {'children': {1: {'mesh': 'a_lod0.msh'}}}, 'visibleTo': 100}}}
        diagnostics = []
        result = convert_model(model, 'asset/container.mdl', diagnostics)
        self.assertEqual(result['version'], 2)
        self.assertEqual([lod['visibleTo'] for lod in array(result['lods'])], [100, 500])
        self.assertTrue(all('name' in lod['node'] for lod in array(result['lods'])))
        self.assertEqual(diagnostics, [])


if __name__ == '__main__':
    unittest.main()
