from pathlib import Path
import tempfile
import unittest

from converter.adapters.game_assets import SiblingIndex, game_input
from converter.pipeline import analyze, convert
from converter.parsers.lua import parse, dumps
from test_conversion import fixture


class LegacySoundsetTests(unittest.TestCase):
    def test_string_and_blank_soundsets_survive_dependency_collection_and_conversion(self):
        for sound in ('waggon_old', ' '):
            with self.subTest(sound=sound), tempfile.TemporaryDirectory() as td:
                root = Path(td) / 'mod'
                fixture(root)
                model = root / 'res/models/model/vehicle/train/unit.mdl'
                data = parse(model.read_text('utf-8'))
                data['metadata']['railVehicle']['soundSet'] = sound
                model.write_text(dumps(data), encoding='utf-8')
                original = model.read_bytes()
                # An explicit sibling index forces the dependency scanner,
                # which was the first place these real MODs crashed.
                siblings = SiblingIndex(Path(td))
                analysis = analyze(root, siblings=siblings)
                expected = {'name': 'waggon_old'} if sound.strip() else {}
                self.assertEqual(analysis['parsed'][model.relative_to(root).as_posix()]['metadata']['railVehicle']['soundSet'], expected)
                result = convert(root, Path(td) / 'out', siblings=siblings, texture_workers=60)
                self.assertEqual(result['status'], 'success', result['diagnostics'])
                self.assertEqual(model.read_bytes(), original)

    def test_string_soundset_imports_custom_script_from_sibling_without_source_changes(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / 'mod'
            fixture(root)
            model = root / 'res/models/model/vehicle/train/unit.mdl'
            data = parse(model.read_text('utf-8'))
            data['metadata']['railVehicle']['soundSet'] = ' custom_waggon '
            model.write_text(dumps(data), encoding='utf-8')
            script = Path(td) / 'provider/res/config/sound_set/custom_waggon.lua'
            script.parent.mkdir(parents=True)
            script.write_text('function data() return {} end', encoding='utf-8')
            original = script.read_bytes()
            with game_input(root, siblings=SiblingIndex(Path(td)), exclude=root) as (overlay, info):
                self.assertIn('res/config/sound_set/custom_waggon.lua', info['game_soundsets'])
                self.assertEqual(info['game_soundsets']['res/config/sound_set/custom_waggon.lua']['text'], original.decode('utf-8'))
            self.assertEqual(script.read_bytes(), original)
            self.assertFalse((root / 'res/config/sound_set').exists())


if __name__ == '__main__':
    unittest.main()


class Tpf2ParameterNameTests(unittest.TestCase):
    SOURCE = ('local soundsetutil = require "soundsetutil"\n'
              'function data()\n  local data = soundsetutil.makeSoundSet()\n'
              '  soundsetutil.addTrackParam01(data, "a.wav", 20.0, { { 0, 0 }, { 1, 1 } }, { { 0, 1 }, { 1, 1.15 } }, "speed01")\n'
              '  soundsetutil.addSimpleTrackParam01(data, "a.wav", 20.0, "power01")\n'
              '  soundsetutil.addEventParam01(data, "k", { "a.wav" }, 5.0, { { 0, 0 } }, { { 0, 1 } }, "speed01")\n'
              '  soundsetutil.addTrackSqueal(data, "a.wav", 20.0)\n  return data\nend\n')

    def test_bare_parameter_names_become_vehicle_info_pairs(self):
        from unittest import mock
        from converter.transforms.sounds import convert_game_sound
        with mock.patch('converter.transforms.sounds.tpf3_shared_sound', return_value='/_assets/audio/a.wav'):
            text = convert_game_sound(self.SOURCE, 'res/config/sound_set/x.lua', {}, {})
        self.assertEqual(text.count('{ "vehicle", "speed01" }'), 2, text)
        self.assertEqual(text.count('{ "vehicle", "power01" }'), 1, text)
        self.assertNotIn(', "speed01")', text)
        self.assertIn('addTrackSqueal(data, "/_assets/audio/a.wav" , 20.0)', text)
