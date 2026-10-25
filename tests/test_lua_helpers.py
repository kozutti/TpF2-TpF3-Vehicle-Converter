import unittest

from converter.parsers.lua import LuaParseError, parse, parse_mod_metadata


class VehicleUtilTests(unittest.TestCase):
    SOURCE = '''local vec2 = require "vec2"
local vehicleutil = require "vehicleutil"
function data()
return {
	animations = {
		drive = vehicleutil.makePistonAnim(vec2.new(0, 0.7201), vec2.new(0, 1.02878), vec2.new(2.61301, 0.851324)),
		rod = vehicleutil.makeConnectingRodAnim(vec2.new(0, 0.7201), vec2.new(0, 1.02878), vec2.new(2.61301, 0.851324)),
		cr = vehicleutil.makeCouplingRodAnim(vec2.new(0, 0.7201), vec2.new(0, 1.02878)),
	},
}
end'''

    def test_piston_and_rod_helpers_become_keyframe_tables(self):
        animations = parse(self.SOURCE)['animations']
        for name in ('drive', 'rod', 'cr'):
            animation = animations[name]
            self.assertEqual(animation['type'], 'KEYFRAME_MATRIX')
            frames = animation['params']['keyframes']
            self.assertEqual(len(frames), 17)
            self.assertEqual(len(frames[1]['transf']), 16)
            self.assertAlmostEqual(frames[1]['time'], 0.0)
            self.assertGreater(frames[17]['time'], 0.0)
        # the piston only moves along x; its first frame is the start pose (no offset)
        self.assertAlmostEqual(animations['drive']['params']['keyframes'][1]['transf'][13], 0.0, places=6)

    def test_other_vehicleutil_calls_stay_errors(self):
        with self.assertRaises(LuaParseError):
            parse(self.SOURCE.replace('makePistonAnim', 'makeSomethingElse'))


class NilGuardedBlockTests(unittest.TestCase):
    def test_if_on_an_undefined_global_is_skipped_like_tpf2_does(self):
        text = '''local result = { a = 1 }

if merk_vehicleUtil and merk_vehicleUtil[1] then
	merk_vehicleUtil[1].getMeshData("x", "y.msh", result)
	if result then print("nested") end
end

function data()
	return result
end'''
        self.assertEqual(parse(text), {'a': 1})

    def test_trailing_code_that_is_not_such_a_block_is_still_refused(self):
        with self.assertRaises(LuaParseError):
            parse('local result = { a = 1 }\nresult.b = 2\nfunction data() return result end')


class ModLuaTests(unittest.TestCase):
    def test_translation_of_an_undefined_name_is_nil_not_an_error(self):
        undefined = []
        info = parse_mod_metadata('function data()\n return { info = { name = _(info_name), description = _("d") } }\nend',
                                  'mod.lua', undefined=undefined)['info']
        self.assertEqual(info, {'description': 'd'})
        self.assertEqual([name for name, _ in undefined], ['info_name'])


if __name__ == '__main__':
    unittest.main()
