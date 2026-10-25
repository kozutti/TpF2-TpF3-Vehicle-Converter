from copy import deepcopy
import unittest

from converter.adapters.tpf3_writer import convert_model, nodes
from converter.resolver import array, table
from tests.test_conversion import IDENTITY


WHEEL = 'vehicle/train/wheel_lod0.msh'


def world_matrices(model):
    """Independent column-major multiplication for checking geometry/anchors."""
    result = {}
    def visit(node, parent):
        local = array(node.get('transf', IDENTITY))
        matrix = [sum(parent[k * 4 + r] * local[c * 4 + k] for k in range(4))
                  for c in range(4) for r in range(4)]
        result[node['name']] = matrix
        for child in array(node.get('children', {})):
            visit(child, matrix)
    visit(array(model['lods'])[0]['node'], array(IDENTITY))
    return result


def vehicle():
    def node(name, mesh=None, x=0, children=None):
        result = {'name': name, 'transf': deepcopy(IDENTITY)}
        result['transf'][13] = x
        if mesh:
            result.update(mesh=mesh, materials=table(['vehicle/train/plain.mtl']))
        if children:
            result['children'] = table(children)
        return result
    bogies = [node('BogieA', 'vehicle/train/bogie.msh', 6, [node('WheelA', WHEEL, -1), node('WheelB', WHEEL, 1)]),
              node('BogieB', 'vehicle/train/bogie.msh', -6, [node('WheelC', WHEEL, -1), node('WheelD', WHEEL, 1)])]
    hidden = node('Wheel_Fake', WHEEL)
    hidden['transf'] = table([0]*15 + [1])
    body = node('Body', children=[node('shell', 'vehicle/train/body.msh'), *bogies])
    root = node('RootNode', children=[body, node('connector', children=[hidden])])
    # Original zero-based TPF2 node IDs: RootNode=0, Body=1, shell=2 ...
    return {'version': 1, 'lods': table([{'node': root, 'visibleFrom': 0, 'visibleTo': 2000}]),
            'metadata': {'railVehicle': {'configs': table([{'axles': table([WHEEL])}]),
                                        'engines': {}, 'topSpeed': 40, 'weight': 25},
                         'transportVehicle': {'carrier': 'RAIL', 'compartmentsList': {}},
                         'seatProvider': {'seats': table([{'group': 1, 'transf': deepcopy(IDENTITY)}])},
                         'cameraConfig': {'cameras': table([{'group': 1, 'transf': deepcopy(IDENTITY)}])}}}


class BogieHierarchyTests(unittest.TestCase):
    def assert_world_equal(self, before, after):
        self.assertEqual(set(before), set(after))
        for name in before:
            for original, actual in zip(before[name], after[name]):
                self.assertAlmostEqual(original, actual, places=8, msg=name)

    def test_zero_scale_helper_is_not_registered_and_real_bogies_are_root_siblings(self):
        original = vehicle()
        snapshot = deepcopy(original)
        diagnostics = []
        result = convert_model(original, 'car.mdl', diagnostics)
        self.assertEqual(original, snapshot)
        self.assertEqual(array(result['metadata']['railVehicle']['config']['axles']), ['WheelA', 'WheelB', 'WheelC', 'WheelD'])
        root = result['lods'][1]['node']
        self.assertEqual({n['name'] for n in array(root['children'])}, {'Body', 'connector', 'BogieA', 'BogieB'})
        self.assert_world_equal(world_matrices(original), world_matrices(result))
        self.assertEqual(result['metadata']['seatProvider']['seats'][1]['group'], 'Body')
        self.assertEqual(result['metadata']['cameraConfig']['cameras'][1]['group'], 'Body')
        codes = {d['code'] for d in diagnostics}
        self.assertIn('INVISIBLE_AXLES_NOT_REGISTERED', codes)
        self.assertIn('BOGIES_PROMOTED_TO_ROOT', codes)
        self.assertNotIn('BOGIE_LAYOUT_UNSUPPORTED', codes)

    def test_static_rotated_and_translated_ancestor_preserves_every_world_frame(self):
        original = vehicle()
        body = original['lods'][1]['node']['children'][1]
        body['transf'] = table([0, 1, 0, 0, -1, 0, 0, 0, 0, 0, 1, 0, 3, 2, 1, 1])
        result = convert_model(original, 'car.mdl', [])
        self.assert_world_equal(world_matrices(original), world_matrices(result))
        promoted = {n['name']: n for n in array(result['lods'][1]['node']['children'])}
        self.assertEqual(array(promoted['BogieA']['transf'])[12:15], [3, 8, 1])
        self.assertEqual(array(promoted['BogieB']['transf'])[12:15], [3, -4, 1])
        self.assertIn('BogieA', [n['name'] for n in array(result['lods'][1]['node']['children'])])

    def test_animated_skinned_scaled_and_articulated_ancestors_keep_their_hierarchy(self):
        for kind in ('animated', 'skin', 'scale', 'fake_bogies'):
            with self.subTest(kind=kind):
                original = vehicle()
                body = original['lods'][1]['node']['children'][1]
                if kind == 'animated':
                    body['animations'] = {'drive': {'type': 'FILE_REF', 'params': {'id': 'body.ani'}}}
                elif kind == 'skin':
                    body['skin'] = 'skin'
                elif kind == 'scale':
                    body['transf'][1] = 2
                else:
                    original['metadata']['railVehicle']['configs'][1]['fakeBogies'] = table([{'group': 1, 'offset': 0, 'position': 0}])
                diagnostics = []
                result = convert_model(original, 'car.mdl', diagnostics)
                self.assertNotIn('BOGIES_PROMOTED_TO_ROOT', {d['code'] for d in diagnostics})
                self.assertNotIn('BogieA', [n['name'] for n in array(result['lods'][1]['node']['children'])])
                self.assert_world_equal(world_matrices(original), world_matrices(result))

    def test_a_helper_visible_in_another_lod_or_animated_is_still_registered(self):
        for kind in ('another_lod', 'animated', 'skin'):
            with self.subTest(kind=kind):
                original = vehicle()
                hidden = original['lods'][1]['node']['children'][2]['children'][1]
                if kind == 'another_lod':
                    original['lods'][2] = deepcopy(original['lods'][1])
                    original['lods'][2]['node']['children'][2]['children'][1]['transf'] = deepcopy(IDENTITY)
                elif kind == 'animated':
                    hidden['animations'] = {'drive': {'type': 'FILE_REF', 'params': {'id': 'wheel.ani'}}}
                else:
                    hidden['skinWeights'] = {1: 1}
                result = convert_model(original, 'car.mdl', [])
                self.assertIn('Wheel_Fake', array(result['metadata']['railVehicle']['config']['axles']))

    def test_stock_root_sibling_bogies_are_unchanged(self):
        original = vehicle()
        root = original['lods'][1]['node']
        body = root['children'][1]
        root['children'] = table([body, *array(body['children'])[1:]])
        body['children'] = table([body['children'][1]])
        diagnostics = []
        result = convert_model(original, 'car.mdl', diagnostics)
        self.assert_world_equal(world_matrices(original), world_matrices(result))
        self.assertNotIn('BOGIES_PROMOTED_TO_ROOT', {d['code'] for d in diagnostics})


class TwoCarModelTests(unittest.TestCase):
    """Mc250+M251 (MOD 9000000008): two body groups in one model, each holding its own two bogies.

    The engine turns a group that holds its own axles, so the bodies bend through curves. Moving the bogies to the root made
    both bodies rigid (seen in game), so this layout must stay as it is."""
    def two_car_model(self):
        original = vehicle()
        root = original['lods'][1]['node']
        body = root['children'][1]
        second = deepcopy(body)
        second['name'] = 'Body2'
        second['transf'][13] = 20
        for node in array(second['children']):
            node['name'] += '2'
            for wheel in array(node.get('children', {})):
                wheel['name'] += '2'
        root['children'] = table([body, second, *array(root['children'])[1:]])
        return original

    def test_body_groups_that_hold_their_own_bogies_keep_them(self):
        original = self.two_car_model()
        diagnostics = []
        result = convert_model(original, 'car.mdl', diagnostics)
        names = {n['name'] for n in array(result['lods'][1]['node']['children'])}
        self.assertFalse(names & {'BogieA', 'BogieB', 'BogieA2', 'BogieB2'})
        self.assertNotIn('BOGIES_PROMOTED_TO_ROOT', {d['code'] for d in diagnostics})
        self.assertIn('BOGIE_LAYOUT_UNSUPPORTED', {d['code'] for d in diagnostics})


class BodyGroupOriginTests(unittest.TestCase):
    """Two body groups, both with their origin at the model origin, each holding its own two bogies (M250 / EH200)."""
    CENTRES = {'Car1': 10.0, 'Car2': -10.0}

    def model(self):
        def node(name, mesh=None, x=0.0, children=None):
            result = {'name': name, 'transf': deepcopy(IDENTITY)}
            result['transf'][13] = x  # IDENTITY is a 1-based table: key 13 is the x translation
            if mesh:
                result.update(mesh=mesh, materials=table(['vehicle/train/plain.mtl']))
            if children:
                result['children'] = table(children)
            return result
        def car(name, centre):
            tag = name[-1]
            return node(name, children=[
                node('shell' + tag, 'vehicle/train/body.msh', centre),
                node('BogieA' + tag, 'vehicle/train/bogie.msh', centre + 6, [node('WA' + tag, WHEEL, -1), node('WB' + tag, WHEEL, 1)]),
                node('BogieB' + tag, 'vehicle/train/bogie.msh', centre - 6, [node('WC' + tag, WHEEL, -1), node('WD' + tag, WHEEL, 1)])])
        root = node('RootNode', children=[car('Car1', 10.0), car('Car2', -10.0)])
        flat = [n['name'] for n, _ in nodes(root)]
        def slot(name):
            transf = deepcopy(IDENTITY); transf[13] = self.CENTRES[name] + 1.0
            return {'group': flat.index(name), 'transf': transf}
        return {'version': 1, 'lods': table([{'node': root, 'visibleFrom': 0, 'visibleTo': 2000}]),
                'metadata': {'railVehicle': {'configs': table([{'axles': table([WHEEL])}]),
                                            'engines': {}, 'topSpeed': 40, 'weight': 25},
                             'transportVehicle': {'carrier': 'RAIL', 'compartmentsList': {}},
                             'seatProvider': {'seats': table([slot('Car1')])},
                             'cameraConfig': {'cameras': table([slot('Car2')])},
                             'cargoSlotProvider': {'slots': table([slot('Car1'), slot('Car2')])}}}, flat

    def test_each_group_origin_moves_to_its_bogie_centre_and_every_node_keeps_its_place(self):
        original, flat = self.model()
        # shells and bogies were written at their model position, so the groups still need their origin moved there
        diagnostics = []
        result = convert_model(deepcopy(original), 'car.mdl', diagnostics)
        before, after = world_matrices(original), world_matrices(result)
        root = result['lods'][1]['node']
        groups = {n['name']: n for n in array(root['children'])}
        self.assertAlmostEqual(array(groups['Car1']['transf'])[12], 10.0)
        self.assertAlmostEqual(array(groups['Car2']['transf'])[12], -10.0)
        for name in set(before) - {'Car1', 'Car2'}:
            for a, b in zip(before[name], after[name]):
                self.assertAlmostEqual(a, b, places=8, msg=name)
        meta = result['metadata']
        seat = meta['seatProvider']['seats'][1]
        self.assertEqual(seat['group'], 'Car1')
        self.assertAlmostEqual(array(seat['transf'])[12], 1.0)
        self.assertAlmostEqual(array(meta['cargoSlotProvider']['slots'][2]['transf'])[12], -9.0 + 10.0)
        camera = meta['cameraConfig']['cameras'][1]
        self.assertEqual(camera['group'], 'Car2')
        self.assertAlmostEqual(array(camera['transf'])[12], -9.0 + 10.0)
        self.assertIn('BODY_GROUP_ORIGINS_CENTERED', {d['code'] for d in diagnostics})

    def head_car(self, front_origin, rear_origin, names=('zF', 'zR')):
        """Kintetsu 1A102: the two bogie groups (each holding its wheels directly) are the only groups under the root, and
        their origins are off the bogies; the intermediate cars of the same MOD have them at the bogie centres."""
        def node(name, mesh=None, pos=(0.0, 0.0, 0.0), children=None):
            result = {'name': name, 'transf': deepcopy(IDENTITY)}
            result['transf'][13], result['transf'][14], result['transf'][15] = pos
            if mesh:
                result.update(mesh=mesh, materials=table(['vehicle/train/plain.mtl']))
            if children:
                result['children'] = table(children)
            return result
        def frame(name, origin, centre):
            # world: wheels at centre +- 1.1 on x, bogie mesh at the centre; every child is written relative to the origin
            def rel(dx):
                return (centre[0] + dx - origin[0], centre[1] - origin[1], 0.467 - origin[2])
            return node(name, pos=origin, children=[node(name + '_bogie', 'vehicle/train/bogie.msh', rel(0.0)),
                                        node(name + '_WF', WHEEL, rel(1.1)), node(name + '_WR', WHEEL, rel(-1.1))])
        root = node('RootNode', children=[node('Body', 'vehicle/train/body.msh'),
                                          frame(names[0], front_origin, (7.0, 0.0, 0.0)), frame(names[1], rear_origin, (-7.0, 0.0, 0.0))])
        flat = [n['name'] for n, _ in nodes(root)]
        return {'version': 1, 'lods': table([{'node': root, 'visibleFrom': 0, 'visibleTo': 2000}]),
                'metadata': {'railVehicle': {'configs': table([{'axles': table([WHEEL])}]),
                                            'engines': {}, 'topSpeed': 40, 'weight': 25},
                             'transportVehicle': {'carrier': 'RAIL', 'compartmentsList': {}},
                             'seatProvider': {'seats': table([{'group': flat.index(names[0]), 'transf': deepcopy(IDENTITY)}])},
                             'cameraConfig': {}}}

    def origins(self, result, names):
        return {n['name']: [round(v, 3) for v in array(n['transf'])[12:15]]
                for n in array(result['lods'][1]['node']['children']) if n['name'] in names}

    def test_a_frame_origin_off_its_wheels_is_moved_to_their_centre_in_all_three_axes(self):
        """MOD 9000000013 kintetsu1A102: zbogie_F at (1.8727, -0.5998, 2.1519), zbogie_R at the model origin."""
        names = ('zbogie_F', 'zbogie_R')
        original = self.head_car((1.8727, -0.5998, 2.1519), (0.0, 0.0, 0.0), names)
        diagnostics = []
        result = convert_model(deepcopy(original), 'car.mdl', diagnostics)
        before, after = world_matrices(original), world_matrices(result)
        self.assertEqual(self.origins(result, names), {'zbogie_F': [7.0, 0.0, 0.0], 'zbogie_R': [-7.0, 0.0, 0.0]})
        for name in set(before) - set(names):
            for a, b in zip(before[name], after[name]):
                self.assertAlmostEqual(a, b, places=8, msg=name)
        self.assertIn('BOGIE_FRAME_ORIGINS_CENTERED', {d['code'] for d in diagnostics})
        seat = result['metadata']['seatProvider']['seats'][1]
        self.assertEqual(seat['group'], 'zbogie_F')
        # the seat sat at the old origin; the group moved to (7, 0, 0), so the seat keeps its place
        self.assertEqual([round(v, 3) for v in array(seat['transf'])[12:15]], [round(1.8727 - 7.0, 3), -0.6, 2.152])

    def test_a_frame_origin_that_is_only_off_along_the_car_is_moved_along_the_car(self):
        original = self.head_car((0.0, 0.0, 0.0), (0.0, 0.0, 0.0))
        diagnostics = []
        result = convert_model(deepcopy(original), 'car.mdl', diagnostics)
        before, after = world_matrices(original), world_matrices(result)
        self.assertEqual(self.origins(result, ('zF', 'zR')), {'zF': [7.0, 0.0, 0.0], 'zR': [-7.0, 0.0, 0.0]})
        for name in set(before) - {'zF', 'zR'}:
            for a, b in zip(before[name], after[name]):
                self.assertAlmostEqual(a, b, places=8, msg=name)
        self.assertIn('BOGIE_FRAME_ORIGINS_CENTERED', {d['code'] for d in diagnostics})

    def test_a_bogie_group_with_an_animated_node_inside_is_left_alone(self):
        original = self.head_car((0.0, 0.0, 0.0), (0.0, 0.0, 0.0))
        group = next(n for n in array(original['lods'][1]['node']['children']) if n['name'] == 'zF')
        array(group['children'])[0]['animations'] = {'parts_on': {'type': 'FILE_REF', 'params': {'id': 'shared/parts_on.ani'}}}
        result = convert_model(original, 'car.mdl', [])
        self.assertEqual(self.origins(result, ('zF',)), {'zF': [0.0, 0.0, 0.0]})

    def test_a_bogie_group_already_at_its_wheels_is_left_alone(self):
        original = self.head_car((7.0, 0.0, 0.0), (-7.0, 0.0, 0.0))
        diagnostics = []
        convert_model(original, 'car.mdl', diagnostics)
        self.assertNotIn('BOGIE_FRAME_ORIGINS_CENTERED', {d['code'] for d in diagnostics})

    def test_a_single_body_group_is_left_alone(self):
        original, _ = self.model()
        root = original['lods'][1]['node']
        root['children'] = table([array(root['children'])[0]])
        diagnostics = []
        result = convert_model(original, 'car.mdl', diagnostics)
        group = array(result['lods'][1]['node']['children'])[0]
        self.assertAlmostEqual(array(group['transf'])[12], 0.0)
        self.assertNotIn('BODY_GROUP_ORIGINS_CENTERED', {d['code'] for d in diagnostics})

    def test_groups_that_must_not_move_stay_put(self):
        for kind in ('animated', 'skin', 'rotated', 'fake_bogie'):
            with self.subTest(kind=kind):
                original, flat = self.model()
                group = array(original['lods'][1]['node']['children'])[0]
                if kind == 'animated':
                    group['animations'] = {'drive': {'type': 'FILE_REF', 'params': {'id': 'body.ani'}}}
                elif kind == 'skin':
                    group['skin'] = 'skin'
                elif kind == 'rotated':
                    group['transf'][5] = 0.5
                else:
                    original['metadata']['railVehicle']['configs'][1]['fakeBogies'] = table([{'group': flat.index('Car1'), 'offset': 0, 'position': 0}])
                diagnostics = []
                result = convert_model(original, 'car.mdl', diagnostics)
                moved = {n['name']: array(n['transf'])[12] for n in array(result['lods'][1]['node']['children'])}
                self.assertAlmostEqual(moved['Car1'], 0.0)
                self.assertNotIn('Car1', next((d.get('groups', {}) for d in diagnostics if d['code'] == 'BODY_GROUP_ORIGINS_CENTERED'), {}))
