from copy import deepcopy
from pathlib import Path
import unittest

from converter.adapters.tpf3_writer import GAME_SOUNDSETS, convert_model, nodes
from converter.parsers.lua import parse, dumps
from converter.resolver import array, table


IDENTITY = table([1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1])


class MetadataMigrationTests(unittest.TestCase):
    def model(self):
        first = {'name': 'wheel', 'mesh': 'wheel_lod0.msh', 'transf': deepcopy(IDENTITY)}
        second = deepcopy(first)
        second['transf'][13] = 6
        root = {'name': 'RootNode', 'transf': deepcopy(IDENTITY), 'children': table([first, second])}
        far = deepcopy(root)
        far['children'] = table(list(reversed(array(far['children']))))
        for node, _ in nodes(far):
            if 'mesh' in node:
                node['mesh'] = 'wheel_lod1.msh'
        return {'version': 1, 'lods': table([{'node': root}, {'node': far}]),
                'metadata': {'railVehicle': {'configs': table([
                    {'axles': table(['wheel_lod0.msh']), 'frontForwardParts': table([2])},
                    {'axles': table(['wheel_lod1.msh'])}])},
                    'seatProvider': {'seats': table([{'group': 2}])},
                    'cameraConfig': {'positions': table([{'group': 1}])},
                    'transportVehicle': {}}}

    def test_same_instances_map_across_reordered_lods_and_numeric_anchors(self):
        model = self.model()
        diagnostics = []
        result = convert_model(model, 'unit.mdl', diagnostics)
        flat = [list(nodes(lod['node'])) for lod in array(result['lods'])]
        self.assertEqual(flat[0][1][0]['name'], flat[1][2][0]['name'])
        self.assertEqual(flat[0][2][0]['name'], flat[1][1][0]['name'])
        for lod in flat:
            names = [node['name'] for node, _ in lod]
            self.assertEqual(len(names), len(set(names)))
        meta = result['metadata']
        self.assertEqual(meta['seatProvider']['seats'][1]['group'], flat[0][2][0]['name'])
        self.assertEqual(meta['cameraConfig']['positions'][1]['group'], flat[0][1][0]['name'])
        self.assertIn('front_forward_parts_on', flat[0][2][0]['animations'])
        self.assertEqual(set(array(meta['railVehicle']['config']['axles'])),
                         {flat[0][1][0]['name'], flat[0][2][0]['name']})
        self.assertFalse(any(d['code'] == 'AMBIGUOUS_NODE_ID' for d in diagnostics))
        self.assertEqual(model['lods'][1]['node']['children'][1]['name'], 'wheel')

    def test_lods_are_ordered_by_visible_to_and_fake_bogies_follow(self):
        model = self.model()
        first, second = model['lods'][1], model['lods'][2]
        first.update(visibleFrom=500, visibleTo=2200)
        second.update(visibleFrom=0, visibleTo=250)
        model['metadata']['railVehicle']['configs'][1]['fakeBogies'] = table(
            [{'group': 1, 'position': 6, 'offset': 0, 'upright': False}])
        result = convert_model(model, 'unit.mdl', [])
        ranges = [(lod['visibleFrom'], lod['visibleTo']) for lod in array(result['lods'])]
        self.assertEqual(ranges, [(0, 250), (500, 2200)])
        bogies = result['metadata']['railVehicle']['config']['fakeBogies']
        self.assertEqual(array(bogies[1]), [])  # the near LOD was the second one in the file
        self.assertEqual(len(array(bogies[2])), 1)  # the far LOD carried the bogie
        self.assertEqual(model['lods'][1]['visibleTo'], 2200, 'the input is not modified')

    def test_exact_duplicate_and_sanitized_name_collision(self):
        model = self.model()
        root = model['lods'][1]['node']
        root['children'][2] = deepcopy(root['children'][1])
        root['children'][3] = {'name': 'wheel_2', 'transf': IDENTITY}
        root['children'][4] = {'name': 'wheel-2', 'transf': IDENTITY}
        result = convert_model(model, 'unit.mdl', [])
        names = [node['name'] for node, _ in nodes(result['lods'][1]['node'])]
        self.assertEqual(len(names), len(set(names)))
        self.assertEqual(len(names), 5)

    def test_particle_node_and_lifetime_properties_are_preserved(self):
        model = self.model()
        emitter = {'child': 2, 'color': table([.6, .6, .6]), 'initialAlpha': .05,
                   'size01': table([.2, 1]), 'frequency': 30, 'lifeTime': 4,
                   'position': table([-9.4, .4, 3.75]), 'velocity': table([0, 0, 3.5]),
                   'velocityDampingFactor': 2.5}
        model['metadata']['particleSystem'] = {'emitters': table([emitter])}
        diagnostics = []
        result = convert_model(model, 'unit.mdl', diagnostics)
        particle = result['metadata']['particleSystem']['emitters'][1]
        self.assertEqual(particle['child'], result['metadata']['seatProvider']['seats'][1]['group'])
        self.assertEqual(particle['frequency'], {'value': 30})
        self.assertEqual(particle['lifeTime'], {'value': 4})
        self.assertEqual(particle['velocity']['value'], emitter['velocity'])
        self.assertEqual(particle['alphaOverLifeTime']['curve'][1]['value'], .05)
        self.assertEqual(particle['sizeOverLifeTime']['curve'][2]['value'], {'value': 1})
        self.assertEqual(particle['colorOverLifeTime']['curve'][1]['value'], emitter['color'])
        self.assertEqual(particle['position'], emitter['position'])
        self.assertFalse(any(d['code'].startswith('PARTICLE') and d['severity'] == 'error' for d in diagnostics))
        self.assertTrue(any(d['code'] == 'PARTICLE_CURVE_ADAPTED' for d in diagnostics))
        self.assertEqual(parse(dumps(result)), result)

    def test_known_sounds_are_explicit_reference_adaptations_unknowns_block(self):
        for sound, game_set in GAME_SOUNDSETS.items():
            model = self.model()
            model['metadata']['railVehicle']['soundSet'] = {'name': sound, 'horn': ''}
            diagnostics = []
            result = convert_model(model, 'unit.mdl', diagnostics)
            self.assertEqual(result['metadata']['soundConfig']['soundSet']['name'], game_set)
            self.assertTrue(game_set.startswith('::/vehicle/') and game_set.endswith('.snd'))
            self.assertTrue(any(d['code'] == 'SOUNDSET_REFERENCE_ADAPTED' and d['severity'] == 'info'
                                for d in diagnostics))
        model['metadata']['railVehicle']['soundSet'] = {'name': 'custom'}
        diagnostics = []
        convert_model(model, 'unit.mdl', diagnostics)
        self.assertTrue(any(d['code'] == 'SOUNDSET_UNMAPPED' for d in diagnostics))


if __name__ == '__main__':
    unittest.main()
