import unittest

from converter.adapters.tpf3_writer import convert_model
from converter.resolver import array, table
from test_singles import tpf2_car


def car(visible_from, visible_to, with_mesh):
    model = tpf2_car(['ELECTRIC'])
    node = {'name': 'RootNode', 'transf': table([1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1])}
    if with_mesh:
        node['children'] = table([{'name': 'body', 'mesh': 'vehicle/train/body.msh', 'materials': table(['vehicle/train/plain.mtl']),
                                   'transf': table([1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1])}])
    model['lods'] = {1: {'node': node, 'visibleFrom': visible_from, 'visibleTo': visible_to}}
    return model


class ZeroLodRangeTests(unittest.TestCase):
    """TPF3 loads a group parent with a mesh and the range 0..0 (MOD 9000000013, checked in game), so 0..0 is left as it is."""

    def test_a_zero_range_with_a_mesh_stays(self):
        diagnostics = []
        result = convert_model(car(0, 0, True), 'menu.mdl', diagnostics)
        lod = array(result['lods'])[0]
        self.assertEqual((lod['visibleFrom'], lod['visibleTo']), (0, 0))
        self.assertFalse({'LOD_RANGE_ZERO_WITH_MESH', 'LOD_RANGE_ADJUSTED'} & {d['code'] for d in diagnostics})

    def test_a_zero_range_without_geometry_stays(self):
        diagnostics = []
        result = convert_model(car(0, 0, False), 'group.mdl', diagnostics)
        lod = array(result['lods'])[0]
        self.assertEqual((lod['visibleFrom'], lod['visibleTo']), (0, 0))

    def test_misspelled_range_keys_are_not_read_as_a_range(self):
        """E8 series ba/fr/11/17 spell the keys `visFFleFrom` / `visFFleTo` = 244.44. TPF2 ignores them (the car is drawn at every
        distance); reading them as a range made the end cars vanish beyond 244 m in game."""
        model = car(0, 0, True)
        lod = array(model['lods'])[0]
        del lod['visibleFrom'], lod['visibleTo']
        lod['visFFleFrom'], lod['visFFleTo'] = 0, 244.44
        diagnostics = []
        result = convert_model(model, 'car.mdl', diagnostics)
        out = array(result['lods'])[0]
        self.assertNotEqual(out.get('visibleTo'), 244.44)
        self.assertNotIn('KEY_MISSPELLED_REPAIRED', {d['code'] for d in diagnostics})

    def test_a_normal_range_is_left_alone(self):
        diagnostics = []
        result = convert_model(car(0, 500, True), 'car.mdl', diagnostics)
        lod = array(result['lods'])[0]
        self.assertEqual((lod['visibleFrom'], lod['visibleTo']), (0, 500))
        self.assertNotIn('LOD_RANGE_ADJUSTED', {d['code'] for d in diagnostics})


if __name__ == '__main__':
    unittest.main()
