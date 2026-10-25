import unittest

from converter.adapters.tpf3_writer import convert_model
from converter.resolver import array, table
from test_singles import tpf2_car

MATRIX = table([1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1])


def label(kind):
    return {'type': kind, 'transf': MATRIX, 'size': table([.5, .15]), 'color': table([1, 1, 1])}


def car_with_labels(*kinds):
    car = tpf2_car(['ELECTRIC'])
    car['lods'][1]['node']['children'] = table([{'name': 'body', 'transf': MATRIX}])
    car['metadata']['labelList'] = {'labels': table([label(kind) for kind in kinds])}
    return car


class TrainNameLabelTests(unittest.TestCase):
    def test_the_train_name_label_is_left_out_and_the_others_stay(self):
        diagnostics = []
        result = convert_model(car_with_labels('NAME', 'NEXT_STOP', 'LINE_NAME'), 'car.mdl', diagnostics)
        kinds = [entry['type'] for entry in array(result['metadata']['labelList']['labels'])]
        self.assertEqual(kinds, ['NEXT_STOP', 'LINE_NAME'])
        self.assertIn('LABEL_NAME_DROPPED', {d['code'] for d in diagnostics})

    def test_every_train_name_label_is_left_out(self):
        result = convert_model(car_with_labels('NAME', 'NAME'), 'car.mdl', [])
        self.assertEqual(array(result['metadata']['labelList']['labels']), [])

    def test_a_car_without_a_train_name_label_is_unchanged(self):
        diagnostics = []
        result = convert_model(car_with_labels('NEXT_STOP'), 'car.mdl', diagnostics)
        self.assertEqual(len(array(result['metadata']['labelList']['labels'])), 1)
        self.assertNotIn('LABEL_NAME_DROPPED', {d['code'] for d in diagnostics})


if __name__ == '__main__':
    unittest.main()
