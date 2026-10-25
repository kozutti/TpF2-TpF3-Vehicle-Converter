import math
from pathlib import Path
import unittest
from unittest.mock import patch

from converter.parsers.lua import LuaParseError, dumps, parse, parse_mod_metadata


class LuaTests(unittest.TestCase):
    def test_constants_and_roundtrip(self):
        result = parse('''-- a comment
        local half = 1/2
        local text = [=[long -- string]=]
        function data() return {
          a = -2^2, b = 2^3^2, ["日本語"] = _('name'),
          {1, 2, half}, c = "a\\n" .. text, ok = true,
        } end''')
        self.assertEqual(result['a'], -4)
        self.assertEqual(result['b'], 512)
        self.assertEqual(result[1], {1: 1, 2: 2, 3: .5})
        self.assertEqual(result, parse(dumps(result)))

    def test_string_escapes(self):
        result = parse(r'''return {s = "\065\x42\u{3042}\z   C", t = [=[
literal]=]}''')
        self.assertEqual(result['s'], 'ABあC')
        self.assertEqual(result['t'], 'literal')
        value = {'s': '\0' + '12', 't': '\\n\n\t"あ'}
        self.assertEqual(parse(dumps(value)), value)

    def test_translation_and_transform(self):
        result = parse('''local transf = require "transf"
        local vec3 = require("vec3")
        return {x = transf.scaleRotZTransl(.9, math.rad(90), vec3.new(1,2,3))}''')['x']
        self.assertEqual([result[i] for i in (13, 14, 15)], [1, 2, 3])
        self.assertAlmostEqual(result[2], .9)
        self.assertAlmostEqual(result[5], -.9)
        self.assertAlmostEqual(result[1], 0)

    def test_non_execution(self):
        for source in ('return {x = os.execute("calc")}', 'os.execute("calc") return {}',
                       'return {x = function() os.execute("calc") end}',
                       'return {} os.execute("calc")', 'local x = require "os" return {}'):
            with self.subTest(source=source), self.assertRaises(LuaParseError) as cm:
                parse(source, 'unsafe.lua')
            self.assertIn('unsafe.lua:1:', str(cm.exception))

    def test_mod_function_extraction(self):
        self.assertEqual(parse('return {runFn = function(settings) end}')['runFn'], {'__empty_function__': True})
        source = '''function data() return {info = {name = "hi"}, runFn = function(settings)
          if settings then for i=1,3 do print("end") end end
        end} end'''
        result = parse_mod_metadata(source)
        self.assertEqual(result['info']['name'], 'hi')
        self.assertIn('print', result['runFn']['__lua_function__'])
        with self.assertRaises(ValueError):
            dumps(result)

    def test_malformed(self):
        for source in ('return {a=1 b=2}', 'return {1,,2}', 'return {a="bad\\q"}',
                       'return {a = 1/0}', 'return {a = 1e999}', 'return {a = true+1}'):
            with self.subTest(source=source), self.assertRaises(LuaParseError):
                parse(source)

    def test_resource_limits(self):
        for source in ('return {a = 2^1000000}', 'return {a = 0x' + 'f' * 129 + '}',
                       'return {a = ' + '(' * 155 + '1' + ')' * 155 + '}'):
            with self.subTest(source=source[:40]), self.assertRaises(LuaParseError):
                parse(source)
        with patch('converter.parsers.lua.MAX_TOKENS', 5), self.assertRaises(LuaParseError):
            parse('return {1,2,3,4}')

    def test_reference_files(self):
        root = Path(__file__).resolve().parents[1]
        files = list((root / 'tpf3参考').rglob('*.msh'))[:3]
        files += list((root / 'tpf2参考').rglob('*.mdl'))[:3]
        files += list((root / 'tpf2参考').rglob('strings.lua'))
        for path in files:
            with self.subTest(path=str(path)):
                result = parse(path.read_text(encoding='utf-8-sig'), str(path))
                self.assertEqual(result, parse(dumps(result)))


if __name__ == '__main__':
    unittest.main()
