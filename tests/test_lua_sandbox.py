from pathlib import Path
import tempfile
import unittest

from converter.parsers import lua_sandbox
from converter.pipeline import convert, validate

from test_conversion import fixture


@unittest.skipUnless(lua_sandbox.available(), 'lupa is not installed')
class SandboxTests(unittest.TestCase):
    def evaluate(self, source, modules=None):
        modules = modules or {}
        return lua_sandbox.evaluate(source, 'x.mdl', lambda name: modules.get(name))

    def test_a_module_of_the_mod_and_the_tpf2_style_helpers_work(self):
        result = self.evaluate('local lib = require "mine.lib"\nlocal transf = require "transf"\nlocal vec3 = require "vec3"\n'
                               'function data() return { value = lib.twice(21), m = transf.transl(vec3.new(1, 2, 3)) } end',
                               {'mine.lib': 'local M = {}\nfunction M.twice(x) return x * 2 end\nreturn M'})
        self.assertEqual(result['value'], 42)
        self.assertEqual([result['m'][i] for i in (13, 14, 15, 16)], [1, 2, 3, 1])

    def test_no_files_processes_or_debugging_are_reachable(self):
        for code in ('os.execute("echo hi")', 'io.open("x")', 'debug.sethook()', 'dofile("x")', 'loadfile("x")',
                     'package.loaded.os.exit()', 'require("os")', 'local f = load("return 1")'):
            with self.assertRaises(lua_sandbox.SandboxError, msg=code):
                self.evaluate(f'function data() {code} return {{}} end')

    def test_an_endless_loop_is_cut_off_and_pcall_cannot_swallow_it(self):
        original = lua_sandbox.INSTRUCTION_LIMIT
        lua_sandbox.INSTRUCTION_LIMIT = 100_000
        try:
            with self.assertRaises(lua_sandbox.SandboxError):
                self.evaluate('function data() while true do end end')
            with self.assertRaises(lua_sandbox.SandboxError):
                self.evaluate('function data() while true do pcall(function() while true do end end) end end')
        finally:
            lua_sandbox.INSTRUCTION_LIMIT = original

    def test_functions_in_the_result_and_non_tables_are_refused(self):
        with self.assertRaises(lua_sandbox.SandboxError):
            self.evaluate('function data() return { f = function() end } end')
        with self.assertRaises(lua_sandbox.SandboxError):
            self.evaluate('function data() return 5 end')

    def test_a_model_generated_by_the_mods_own_lua_converts(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / 'mod'
            fixture(root)
            model = root / 'res/models/model/vehicle/train/unit.mdl'
            body = model.read_text('utf-8').replace('function data()', 'function M.make()', 1)
            library = root / 'res/scripts/gen/unit.lua'
            library.parent.mkdir(parents=True)
            library.write_text('local M = {}\n' + body + '\nreturn M\n', 'utf-8')
            model.write_text('local lib = require "gen.unit"\nfunction data() return lib.make() end\n', 'utf-8')
            result = convert(root, Path(td) / 'out', siblings=None)
            self.assertEqual(result['status'], 'success', [d for d in result['diagnostics'] if d['severity'] == 'error'])
            self.assertIn('LUA_SANDBOX_EVALUATED', {d['code'] for d in result['diagnostics']})
            self.assertTrue(validate(result['package_dir'])['valid'])


if __name__ == '__main__':
    unittest.main()
