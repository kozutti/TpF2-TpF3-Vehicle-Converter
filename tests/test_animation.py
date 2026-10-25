from copy import deepcopy
from pathlib import Path
import tempfile
import unittest

from converter.adapters.animation import convert_animation, normalize_embedded_animation, playback_duration
from converter.adapters.tpf3_writer import write_definition
from converter.parsers.lua import dumps, parse
from converter.resolver import array, table
from converter.validators import validate_package


IDENTITY = table([1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1])


class AnimationTests(unittest.TestCase):
    def test_delayed_door_indicator_holds_until_original_close_time(self):
        on = deepcopy(IDENTITY)
        on[14] = .03
        source = {'times': {1: 2958.333, 2: 3000}, 'transfs': {1: on, 2: IDENTITY}}
        snapshot = deepcopy(source)
        diagnostics = []
        value = convert_animation(source, 'doorlight.ani', diagnostics)
        self.assertEqual(array(value['times']), [0, 2958.333, 3000])
        self.assertEqual(array(value['transfs']), [on, on, IDENTITY])
        self.assertEqual(source, snapshot)
        self.assertEqual(playback_duration(array(source['times'])), 3000)
        self.assertEqual(diagnostics[0]['code'], 'ANIMATION_ZERO_START_INSERTED')
        self.assertEqual(diagnostics[0]['before'], [2958.333, 3000])
        self.assertEqual(diagnostics[0]['after'], [0, 2958.333, 3000])
        self.assertEqual(convert_animation(value, 'doorlight.ani', []), value)

    def test_zero_start_and_invalid_times_are_not_retimed(self):
        for times in ([0, 3000], [-1, 3000], [float('nan'), 3000]):
            source = {'times': table(times), 'transfs': {1: IDENTITY, 2: IDENTITY}}
            diagnostics = []
            value = convert_animation(source, 'test.ani', diagnostics)
            self.assertEqual(len(array(value['times'])), 2)
            self.assertEqual(diagnostics, [])

    def test_embedded_keyframes_duplicate_first_state_without_losing_fields(self):
        for kind in ['KEYFRAME', 'KEYFRAME_MATRIX']:
            animation = {'type': kind, 'forward': False, 'interpolation': 'NEAREST', 'params': {
                'keyframes': {1: {'time': 1000, 'transf': IDENTITY, 'rot': {1: 0}},
                              2: {'time': 2000, 'transf': IDENTITY, 'rot': {1: 1}}}}}
            normalize_embedded_animation(animation, 'test.mdl', '/animation', [])
            frames = array(animation['params']['keyframes'])
            self.assertEqual([frame['time'] for frame in frames], [0, 1000, 2000])
            self.assertEqual(frames[0]['rot'], frames[1]['rot'])
            self.assertFalse(animation['forward'])
            self.assertEqual(animation['interpolation'], 'NEAREST')

    def test_output_validator_rejects_positive_start(self):
        with tempfile.TemporaryDirectory() as td:
            package = Path(td)
            (package / 'mod.json').write_text('{"modId":"test"}', encoding='utf8')
            (package / 'content').mkdir()
            path = package / 'content/indicator.ani'
            source = {'times': {1: 2958.333, 2: 3000}, 'transfs': {1: IDENTITY, 2: IDENTITY}}
            path.write_text(dumps(source), encoding='utf8')
            self.assertIn('ANIMATION_START_NOT_ZERO', [d['code'] for d in validate_package(package)['diagnostics']])
            path.write_text(dumps(convert_animation(source, 'indicator.ani', [])), encoding='utf8')
            self.assertTrue(validate_package(package)['valid'])

    def test_reference_doorlights_convert_without_changing_end_time(self):
        root = Path(__file__).resolve().parents[1] / 'tpf2参考/9000000009/res/models/animation'
        if not root.is_dir():
            self.skipTest('Local reference resources are not distributed with portable build')
        positive = 0
        for path in root.rglob('*.ani'):
            original = parse(path.read_text('utf-8-sig'))
            old_times = array(original.get('times', {}))
            converted = write_definition(original, str(path), {}, {}, 'test', [])
            new_times = array(converted.get('times', {}))
            self.assertEqual(new_times[0], 0, str(path))
            self.assertEqual(new_times[-1], old_times[-1], str(path))
            if old_times[0] > 0:
                positive += 1
                self.assertEqual(new_times[1:], old_times)
        self.assertGreater(positive, 0)


if __name__ == '__main__':
    unittest.main()
