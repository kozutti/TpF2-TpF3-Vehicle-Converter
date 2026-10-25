import unittest

from converter.adapters.animation import convert_animation
from converter.resolver import array, table

IDENTITY = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]


class AnimationLengthTests(unittest.TestCase):
    def convert(self, times, count):
        diagnostics = []
        value = {'times': table(times), 'transfs': table([list(IDENTITY) for _ in range(count)])}
        return convert_animation(value, 'a.ani', diagnostics), diagnostics

    def test_one_extra_transform_is_dropped_with_a_warning(self):
        value, diagnostics = self.convert([0, 100, 200], 4)
        self.assertEqual(len(array(value['times'])), 3)
        self.assertEqual(len(array(value['transfs'])), 3)
        self.assertEqual([(d['code'], d['severity']) for d in diagnostics], [('ANIMATION_LENGTH_TRIMMED', 'warning')])

    def test_one_extra_time_is_dropped_too(self):
        value, diagnostics = self.convert([0, 100, 200, 300], 3)
        self.assertEqual(array(value['times']), [0, 100, 200])
        self.assertEqual(len(array(value['transfs'])), 3)
        self.assertIn('ANIMATION_LENGTH_TRIMMED', {d['code'] for d in diagnostics})

    def test_matching_lengths_are_untouched(self):
        value, diagnostics = self.convert([0, 100], 2)
        self.assertEqual(diagnostics, [])
        self.assertEqual(array(value['times']), [0, 100])


if __name__ == '__main__':
    unittest.main()

