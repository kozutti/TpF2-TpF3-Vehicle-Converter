from copy import deepcopy
import math
import unittest

from converter.adapters.tpf3_writer import attach_root_particles_to_body, convert_model, nodes
from converter.adapters.bogies import IDENTITY, matrix, multiply, walk
from converter.resolver import array, table
from tests.test_singles import tpf2_car


class ParticleCantAttachmentTests(unittest.TestCase):
    def lods(self):
        root = {'name': 'RootNode', 'transf': table(IDENTITY), 'children': table([
            {'name': 'body', 'mesh': 'body_lod0.msh', 'transf': table(IDENTITY)}])}
        far = deepcopy(root)
        far['children'][1]['mesh'] = 'body_lod1.msh'
        return [{'node': root}, {'node': far}]

    def system(self):
        return {'emitters': table([{'child': 'RootNode', 'position': table([8, -.44, 4.05]),
                                    'velocity': {'value': table([0, 0, 3.3])}, 'frequency': {'value': 12},
                                    'lifeTime': {'value': 2}, 'particleId': ''}])}

    def point(self, lod, emitter):
        for node, branch in walk(lod['node']):
            if node['name'] == emitter['child']:
                frame = IDENTITY[:]
                for ancestor in branch:
                    frame = multiply(frame, matrix(ancestor))
                pos = array(emitter['position']) + [1]
                return [sum(frame[c*4+r]*pos[c] for c in range(4)) for r in range(3)]
        self.fail('Emitter anchor not found')

    def test_origin_body_keeps_emitter_and_model_data_but_follows_roll(self):
        lods, system = self.lods(), self.system()
        old_lods, old_emitter = deepcopy(lods), deepcopy(system['emitters'][1])
        diagnostics = []
        attach_root_particles_to_body(system, lods, 'car.mdl', diagnostics)
        emitter = system['emitters'][1]
        self.assertEqual(emitter['child'], 'body')
        self.assertEqual({k:v for k,v in emitter.items() if k != 'child'},
                         {k:v for k,v in old_emitter.items() if k != 'child'})
        self.assertEqual(lods, old_lods)
        for lod in lods:
            self.assertEqual(self.point(lod, emitter), [8, -.44, 4.05])
            # Apply a body roll to expose a root-vs-body attachment difference.
            angle = math.radians(5)
            roll = IDENTITY[:]
            roll[5], roll[6], roll[9], roll[10] = math.cos(angle), math.sin(angle), -math.sin(angle), math.cos(angle)
            lod['node']['children'][1]['transf'] = table(roll)
            new_point = self.point(lod, emitter)
            self.assertAlmostEqual(new_point[1], -.44*math.cos(angle)-4.05*math.sin(angle))
            self.assertEqual(self.point(lod, old_emitter), [8, -.44, 4.05])
        self.assertEqual([d['code'] for d in diagnostics], ['PARTICLES_ATTACHED_TO_BODY_FRAME'])

    def test_unsafe_or_missing_body_gets_an_identity_frame_in_every_lod(self):
        for variant in ('translated', 'rotated', 'animated', 'far_missing', 'nested'):
            with self.subTest(variant=variant):
                lods, system = self.lods(), self.system()
                body = lods[0]['node']['children'][1]
                if variant == 'translated': body['transf'][13] = 2
                if variant == 'rotated': body['transf'][1] = body['transf'][6] = -1
                if variant == 'animated': body['animations'] = {'move': {'type': 'FILE_REF'}}
                if variant == 'far_missing': lods[1]['node']['children'][1]['name'] = 'other_body'
                if variant == 'nested':
                    lods[0]['node']['children'] = table([{'name': 'offset', 'transf': table(IDENTITY), 'children': table([body])}])
                    lods[0]['node']['children'][1]['transf'][13] = 3
                old_children = [deepcopy(lod['node']['children']) for lod in lods]
                attach_root_particles_to_body(system, lods, 'car.mdl', [])
                emitter = system['emitters'][1]
                self.assertEqual(emitter['child'], 'particle_root')
                for lod, old in zip(lods, old_children):
                    self.assertEqual(array(lod['node']['children'])[:-1], array(old))
                    self.assertEqual(self.point(lod, emitter), [8, -.44, 4.05])
                once = deepcopy((system,lods))
                self.assertIsNone(attach_root_particles_to_body(system, lods, 'car.mdl', []))
                self.assertEqual((system,lods), once)

    def test_named_non_root_emitters_and_models_without_emitters_are_untouched(self):
        for child in ('body', 'bogie', 'exhaust_nozzle'):
            with self.subTest(child=child):
                lods, system = self.lods(), self.system()
                system['emitters'][1]['child'] = child
                before = deepcopy((system,lods))
                diagnostics=[]
                attach_root_particles_to_body(system, lods, 'car.mdl', diagnostics)
                self.assertEqual((system,lods), before)
                self.assertEqual(diagnostics, [])
        lods = self.lods()
        before=deepcopy(lods)
        attach_root_particles_to_body({},lods,'car.mdl',[])
        self.assertEqual(lods,before)

    def test_frame_name_is_unique_in_all_lods_and_root_pose_is_preserved(self):
        lods, system = self.lods(), self.system()
        for i,lod in enumerate(lods):
            lod['node']['children'][1]['name'] = 'offset_body'
            lod['node']['children'][i+2] = {'name': 'particle_root' if i==0 else 'particle_root_1'}
            lod['node']['transf'][13] = 10
        attach_root_particles_to_body(system, lods, 'car.mdl', [])
        self.assertEqual(system['emitters'][1]['child'], 'particle_root_2')
        for lod in lods:
            names=[n['name'] for n,_ in nodes(lod['node'])]
            self.assertEqual(len(names),len(set(names)))
            self.assertEqual(self.point(lod,system['emitters'][1]),[18,-.44,4.05])

    def test_numeric_root_particle_is_reattached_during_vehicle_conversion(self):
        model=tpf2_car(['DIESEL'])
        model['lods']=table(self.lods())
        model['metadata']['particleSystem']=self.system()
        model['metadata']['particleSystem']['emitters'][1]['child']=0
        # Use legacy fields so the integration path exercises curve conversion.
        emitter=model['metadata']['particleSystem']['emitters'][1]
        emitter['velocity']=table([0,0,3.3]);emitter['frequency']=12;emitter['lifeTime']=2
        diagnostics=[]
        output=convert_model(model,'car.mdl',diagnostics)
        self.assertEqual(output['metadata']['particleSystem']['emitters'][1]['child'],'body')
        self.assertEqual(output['metadata']['particleSystem']['emitters'][1]['position'],table([8,-.44,4.05]))
        self.assertEqual(model['metadata']['particleSystem']['emitters'][1]['child'],0)
        self.assertIn('PARTICLES_ATTACHED_TO_BODY_FRAME',{d['code'] for d in diagnostics})


if __name__ == '__main__':
    unittest.main()
