import unittest

from converter.adapters.tpf3_writer import convert_model, edit_distance
from converter.resolver import array, table
from test_singles import tpf2_car


def car_with(**changes):
    car = tpf2_car(['DIESEL'])
    car['lods'][1]['node']['children'] = table([{'name': 'body', 'transf': table([1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1])}])
    for key, value in changes.items():
        car['metadata'][key] = value
    return car


SYSTEM = {'emitters': table([{'child': 0, 'position': table([1, 0, 3]), 'lifeTime': 2, 'velocity': 1, 'frequency': 10,
                              'color': table([.2, .2, .2]), 'size01': table([.01, 2.5]), 'initialAlpha': .5}])}


class MisspelledKeyTests(unittest.TestCase):
    def test_edit_distance(self):
        self.assertEqual(edit_distance('oundset', 'soundset'), 1)
        self.assertEqual(edit_distance('particlehkstem', 'particlesystem'), 2)

    def test_a_particle_system_with_a_find_and_replace_typo_is_read_as_one(self):
        diagnostics = []
        result = convert_model(car_with(particlehkstem=SYSTEM), 'car.mdl', diagnostics)
        self.assertEqual(len(array(result['metadata']['particleSystem']['emitters'])), 1)
        repaired = [d for d in diagnostics if d['code'] == 'KEY_MISSPELLED_REPAIRED']
        self.assertEqual(len(repaired), 1)
        self.assertIn('particleSystem', repaired[0]['message'])
        self.assertNotIn('METADATA_UNMAPPED', {d['code'] for d in diagnostics})

    def test_a_sound_set_without_its_first_letter_is_read_as_a_sound_set(self):
        car = car_with()
        rail = car['metadata']['railVehicle']
        rail['oundSet'] = {'name': 'train_electric_modern'}
        diagnostics = []
        result = convert_model(car, 'car.mdl', diagnostics)
        self.assertEqual(result['metadata']['soundConfig']['soundSet']['name'].endswith('.snd'), True)
        self.assertIn('KEY_MISSPELLED_REPAIRED', {d['code'] for d in diagnostics})

    def test_unrelated_keys_and_existing_keys_are_left_alone(self):
        diagnostics = []
        convert_model(car_with(signal={'x': 1}, categoryList={}), 'car.mdl', diagnostics)
        codes = [d['code'] for d in diagnostics]
        self.assertNotIn('KEY_MISSPELLED_REPAIRED', codes)
        self.assertEqual(codes.count('METADATA_UNMAPPED'), 2)
        # the right key is already there: the misspelled one is not allowed to overwrite it
        diagnostics = []
        result = convert_model(car_with(particleSystem=SYSTEM, particlehkstem={'emitters': table([])}), 'car.mdl', diagnostics)
        self.assertEqual(len(array(result['metadata']['particleSystem']['emitters'])), 1)
        self.assertNotIn('KEY_MISSPELLED_REPAIRED', {d['code'] for d in diagnostics})


if __name__ == '__main__':
    unittest.main()
