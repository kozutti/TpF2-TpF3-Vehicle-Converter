import re
import tempfile
import unittest
from pathlib import Path

from converter.parsers.lua import dumps
from converter.pipeline import convert, validate
from converter.transforms.sound_tracks import convert_tracks_soundset, is_tracks_soundset
from tests.test_conversion import fixture

WAV = b'RIFF' + (4).to_bytes(4, 'little') + b'WAVE'
SET_NAME = 'scale_sound'
SOUND_SET = '''local soundeffectsutil = require "soundeffectsutil"
function data()
return {
	tracks = {
		{ name = "vehicle/own/idle.wav", refDist = 10 },
		{ name = "vehicle/own/note01.wav", refDist = 15 },
		{ name = "vehicle/own/brakes.wav", refDist = 20 },
		{ name = "vehicle/own/ringing.wav", refDist = 20 },
	},
	events = {
		clacks = {names = { "vehicle/own/clack.wav", }, refDist = 15 },
		closeDoors = { names = {"vehicle/own/door.wav",}, refDist = 8 },
	},
	updateFn = function (input)
		local axleRefWeight = 10.0
		return {
			tracks = {
				{ gain = soundeffectsutil.sampleCurve({ { 0, 1 }, }, input.speed01), pitch = 1 },---Idle
				{
					gain = soundeffectsutil.sampleCurve({ { 0.0, 0.0 }, { 0.001, 5 }, { 0.08, 0 }, }, input.speed01)
					* soundeffectsutil.sampleCurve({ { 0.03, 0.0 }, { 0.031, 1.0 } }, input.power01),
					pitch = soundeffectsutil.sampleCurve({ { 0.1, 0.8 }, { 0.9, 1.5 }, }, input.speed01)
				},---Note01
				soundeffectsutil.brake(input.speed, input.brakeDecel, 0.3),
				soundeffectsutil.squeal(input.speed, input.sideForce, input.maxSideForce),
			},
		}
	end
}
end
'''


def build(td, text=SOUND_SET):
    root = Path(td) / 'mod'
    model = fixture(root)
    (root / 'res/config/sound_set').mkdir(parents=True)
    (root / 'res/audio/effects/vehicle/own').mkdir(parents=True)
    (root / f'res/config/sound_set/{SET_NAME}.lua').write_text(text, 'utf-8')
    for name in ('idle', 'note01', 'brakes', 'ringing', 'clack', 'door'):
        (root / f'res/audio/effects/vehicle/own/{name}.wav').write_bytes(WAV)
    model['metadata']['railVehicle']['soundSet'] = {'name': SET_NAME}
    (root / 'res/models/model/vehicle/train/unit.mdl').write_text(dumps(model), 'utf-8')
    return root


class TracksSoundSetTests(unittest.TestCase):
    def test_detects_the_updatefn_form(self):
        self.assertTrue(is_tracks_soundset(SOUND_SET))
        self.assertFalse(is_tracks_soundset('local soundsetutil = require "soundsetutil"\nfunction data() end'))

    def test_every_callback_reference_carries_the_mod_id(self):
        files = {'res/audio/effects/vehicle/own/' + n + '.wav': {} for n in ('idle', 'note01', 'brakes', 'ringing', 'clack', 'door')}
        mapping = {p: '_assets/' + p[4:] for p in files}
        snd, script = convert_tracks_soundset(SOUND_SET, 'res/config/sound_set/x.lua', files, mapping, 'my_mod', '_assets/generated/sound/x.script')
        refs = re.findall(r'"([^"]*\.script@[^"]*)"', snd)
        self.assertEqual(refs, ['my_mod::/_assets/generated/sound/x.script@track_1', 'my_mod::/_assets/generated/sound/x.script@track_2'])
        self.assertIn('result.track_1 = function', script)
        self.assertIn('result.track_2 = function', script)
        self.assertIn('addTrackBrake(data, "/_assets/audio/effects/vehicle/own/brakes.wav", 20.0, 0.3)', snd)
        self.assertIn('addTrackSqueal(data, "/_assets/audio/effects/vehicle/own/ringing.wav", 20.0)', snd)
        self.assertIn('10000.0', snd)  # TPF2 states the axle weight in tonnes, TPF3 in kilograms

    def test_input_values_and_helpers_are_mapped(self):
        files = {'res/audio/effects/vehicle/own/' + n + '.wav': {} for n in ('idle', 'note01', 'brakes', 'ringing', 'clack', 'door')}
        mapping = {p: '_assets/' + p[4:] for p in files}
        _, script = convert_tracks_soundset(SOUND_SET, 'res/config/sound_set/x.lua', files, mapping, 'm', 'p/x.script')
        self.assertIn('audioutil.sampleCurve({{0.0, 0.0}, {0.001, 5}, {0.08, 0},}, (vehicle.speed01 or 0))', script)
        self.assertIn('(vehicle.power01 or 0)', script)
        self.assertNotIn('soundeffectsutil', script)
        self.assertNotIn('input.', script)

    def test_railvehicle_inputs_are_mapped_with_units(self):
        # measured in the game (tools/sound_probe): sideForce/maxSideForce/numAxles/gameSpeedUp/weight come from railVehicle, weight in kg
        text = SOUND_SET.replace('input.power01', 'input.sideForce / input.maxSideForce + input.weight / input.numAxles + input.gameSpeedUp + input.topSpeed')
        _, script = self.convert(text)
        self.assertIn('local railVehicle = currentInfo.railVehicle or {}', script)
        self.assertIn('(railVehicle.sideForce or 0)', script)
        self.assertIn('(railVehicle.maxSideForce or 0)', script)
        self.assertIn('((railVehicle.weight or 0) / 1000)', script)  # TPF2 states weights in tonnes, TPF3 in kg
        self.assertIn('(railVehicle.numAxles or 1)', script)  # never 0: the input is used as a divisor
        self.assertIn('(railVehicle.gameSpeedUp or 1)', script)
        self.assertIn('(vehicle.topSpeed or 1)', script)
        self.assertNotIn('input.', script)

    def test_power_output_is_the_signed_fraction_of_the_rated_power(self):
        # TPF3 reports kW; TPF2 MODs feed the value to curves over -1..1 (Taurus sets), so it is divided by the rated power
        _, script = self.convert(SOUND_SET.replace('input.power01', 'input.powerOutput'))
        self.assertIn('(vehicle.power or 0) > 0 and (vehicle.powerOutput or 0) / vehicle.power or 0', script)  # never divides by 0
        self.assertNotIn('input.', script)

    def test_unknown_expression_is_refused(self):
        bad = SOUND_SET.replace('input.power01', 'input.somethingNew')
        with self.assertRaises(ValueError):
            convert_tracks_soundset(bad, 'x.lua', {}, {}, 'm', 'p/x.script')
        with self.assertRaises(ValueError):
            convert_tracks_soundset(SOUND_SET.replace('pitch = 1 }', 'pitch = os.time() }'), 'x.lua', {}, {}, 'm', 'p/x.script')

    def test_fewer_returned_tracks_than_declared_is_refused(self):
        bad = SOUND_SET.replace('soundeffectsutil.squeal(input.speed, input.sideForce, input.maxSideForce),', '')
        with self.assertRaises(ValueError):
            convert_tracks_soundset(bad, 'x.lua', {}, {}, 'm', 'p/x.script')

    def test_extra_returned_tracks_are_ignored_like_tpf2(self):
        # class156 declares 2 tracks and returns 4; TPF2 pairs them by position and drops the rest.
        fewer = SOUND_SET.replace('\t\t{ name = "vehicle/own/brakes.wav", refDist = 20 },\n\t\t{ name = "vehicle/own/ringing.wav", refDist = 20 },\n', '')
        self.assertNotIn('brakes.wav', fewer)
        snd, _ = self.convert(fewer)
        self.assertEqual(snd.count('addTrackCustom'), 2)
        self.assertNotIn('addTrackBrake', snd)
        self.assertNotIn('addTrackSqueal', snd)

    @staticmethod
    def convert(text):
        files = {'res/audio/effects/vehicle/own/' + n + '.wav': {} for n in ('idle', 'note01', 'brakes', 'ringing', 'clack', 'door')}
        mapping = {p: '_assets/' + p[4:] for p in files}
        return convert_tracks_soundset(text, 'res/config/sound_set/x.lua', files, mapping, 'm', 'p/x.script')

    @staticmethod
    def with_events(events):
        marker = '\t\t\t},\n\t\t}\n\tend'
        assert marker in SOUND_SET
        return SOUND_SET.replace(marker, '\t\t\t},\n\t\t\tevents = {\n' + events + '\n\t\t\t},\n\t\t}\n\tend')

    def test_event_gain_and_pitch_from_updatefn_are_kept(self):
        # 377 (9000000005) plays its door sound at 8x, class156 at 3x / pitch 0.9: TPF3 would otherwise use 1 / 1.
        text = self.with_events('\t\t\t\tclacks = soundeffectsutil.clacks(input.speed, input.weight, input.numAxles, axleRefWeight, input.gameSpeedUp),\n'
                                '\t\t\t\tcloseDoors = { gain = 3.0, pitch = 0.9 },')
        snd, _ = self.convert(text)
        self.assertIn('addEventParam01(data, "closeDoors", {', snd)
        self.assertIn('{ { 0, 3.0 } }, { { 0, 0.9 } }, { "vehicle", "speed01" })', snd)
        self.assertIn('addEventClacks', snd)

    def test_default_or_computed_event_values_stay_plain_events(self):
        for override in ('closeDoors = { gain = 1.0, pitch = 1.0 },', 'closeDoors = { gain = input.speed, pitch = 1.0 },'):
            snd, _ = self.convert(self.with_events('\t\t\t\t' + override))
            self.assertIn('addEvent(data, "closeDoors"', snd)
            self.assertNotIn('addEventParam01', snd)

    def test_event_gain_survives_the_whole_conversion_and_validates(self):
        text = self.with_events('\t\t\t\tcloseDoors = { gain = 3.0, pitch = 0.9 },')
        with tempfile.TemporaryDirectory() as td:
            result = convert(build(td, text), Path(td) / 'out', siblings=None)
            self.assertEqual(result['status'], 'success', [d for d in result['diagnostics'] if d['severity'] == 'error'])
            package = Path(result['package_dir'])
            snd = (package / f'content/_assets/generated/sound/{SET_NAME}.snd.lua').read_text('utf-8')
            self.assertIn('addEventParam01(data, "closeDoors"', snd)
            self.assertTrue(validate(package)['valid'])


class PlainSoundSetClackWeightTests(unittest.TestCase):
    TEXT = '''local soundsetutil = require "soundsetutil"
function data()
local data = soundsetutil.makeSoundSet()
soundsetutil.addEventClacks(data, { "vehicle/own/clack.wav" }, 15.0, 10.0)
soundsetutil.addEventClacks(data, { "vehicle/own/clack.wav" }, 15.0, 10.0 * 1000.0)
return data
end
'''

    def test_axle_reference_weight_is_converted_from_tonnes_to_kilograms(self):
        import converter.transforms.sounds as sounds
        original = sounds.resolve
        sounds.resolve = lambda value, owner, files: 'x'
        try:
            out = sounds.convert_game_sound(self.TEXT, 'o', {}, {'x': 'y.wav'})
        finally:
            sounds.resolve = original
        self.assertIn('15.0, 10000.0 )', out)  # TPF2 tonnes -> TPF3 kilograms
        self.assertIn('15.0, 10.0 * 1000.0)', out)  # already TPF3 style: untouched


class TracksSoundSetPipelineTests(unittest.TestCase):
    def test_converted_package_is_valid_and_references_the_mod(self):
        with tempfile.TemporaryDirectory() as td:
            result = convert(build(td), Path(td) / 'out', siblings=None)
            self.assertEqual(result['status'], 'success', [d for d in result['diagnostics'] if d['severity'] == 'error'])
            codes = {d['code'] for d in result['diagnostics']}
            self.assertIn('GAME_SOUNDSET_IMPORTED', codes)
            self.assertNotIn('GAME_SOUNDSET_UNSUPPORTED', codes)
            package = Path(result['package_dir'])
            sound = package / 'content/_assets/generated/sound'
            self.assertTrue((sound / f'{SET_NAME}.script.lua').is_file())
            snd = (sound / f'{SET_NAME}.snd.lua').read_text('utf-8')
            self.assertIn(f'{package.name}::/_assets/generated/sound/{SET_NAME}.script@track_2', snd)
            self.assertTrue(validate(package)['valid'])

    def test_one_missing_audio_file_becomes_a_stock_sound_and_the_rest_of_the_set_is_kept(self):
        with tempfile.TemporaryDirectory() as td:
            root = build(td)
            (root / 'res/audio/effects/vehicle/own/note01.wav').unlink()
            (root / 'res/audio/effects/vehicle/own/brakes.wav').unlink()
            result = convert(root, Path(td) / 'out', siblings=None)
            self.assertEqual(result['status'], 'success', [d for d in result['diagnostics'] if d['severity'] == 'error'])
            codes = {d['code'] for d in result['diagnostics']}
            self.assertIn('GAME_SOUNDSET_IMPORTED', codes)
            self.assertIn('SOUND_FILE_REPLACED_BY_STOCK', codes)
            self.assertNotIn('GAME_SOUNDSET_UNSUPPORTED', codes)
            note = next(d for d in result['diagnostics'] if d['code'] == 'SOUND_FILE_REPLACED_BY_STOCK')
            self.assertIn('2 件', note['message'])
            package = Path(result['package_dir'])
            snd = (package / f'content/_assets/generated/sound/{SET_NAME}.snd.lua').read_text('utf-8')
            self.assertIn('::/vehicle/train/shared/sound/train/brakes.wav', snd)       # brakes.wav -> the stock brake sound
            self.assertIn('::/vehicle/train/shared/sound/train_electric_modern/drive.wav', snd)  # note01.wav -> the stock drive sound
            self.assertRegex(snd, r'/_assets/[^"]*idle')                                 # the author's own idle sound is still there
            self.assertTrue(validate(package)['valid'])

    def test_reference_without_the_mod_id_is_an_error(self):
        # The bug this guards against: "x.script@track_1" is looked up in the base game and the track never sounds.
        with tempfile.TemporaryDirectory() as td:
            result = convert(build(td), Path(td) / 'out', siblings=None)
            package = Path(result['package_dir'])
            snd = package / f'content/_assets/generated/sound/{SET_NAME}.snd.lua'
            snd.write_text(snd.read_text('utf-8').replace(f'{package.name}::', ''), 'utf-8')
            report = validate(package)
            self.assertFalse(report['valid'])
            self.assertTrue({'SOUND_CALLBACK_REF_INVALID', 'GENERATED_SOUND_SCRIPT_CHANGED'} & {d['code'] for d in report['diagnostics']})

    def test_missing_callback_function_is_an_error(self):
        with tempfile.TemporaryDirectory() as td:
            result = convert(build(td), Path(td) / 'out', siblings=None)
            package = Path(result['package_dir'])
            snd = package / f'content/_assets/generated/sound/{SET_NAME}.snd.lua'
            text = snd.read_text('utf-8').replace('@track_2', '@track_9')
            snd.write_text(text, 'utf-8')
            from converter.transforms.sounds import check_callback_references
            content = package / 'content'
            problems = check_callback_references(content, snd, text)
            self.assertEqual([d['code'] for d in problems], ['SOUND_CALLBACK_FUNCTION_MISSING'])


if __name__ == '__main__':
    unittest.main()
