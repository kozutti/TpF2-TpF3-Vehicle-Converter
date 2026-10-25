from pathlib import Path
import tempfile
import unittest

from converter.pipeline import auto_game_root


class AutoGameRootTests(unittest.TestCase):
    def test_a_workshop_mod_finds_the_tpf2_install_of_its_steam_library(self):
        with tempfile.TemporaryDirectory() as td:
            library = Path(td) / 'SteamLibrary' / 'steamapps'
            mod = library / 'workshop' / 'content' / '1066780' / '123'
            mod.mkdir(parents=True)
            (library / 'common' / 'Transport Fever 2' / 'res').mkdir(parents=True)
            self.assertEqual(Path(auto_game_root(mod, None)), (library / 'common' / 'Transport Fever 2').resolve())
            # nested sub folder inside the workshop folder works too
            nested = library / 'workshop' / 'content' / '1066780' / 'group' / '456'
            nested.mkdir(parents=True)
            self.assertEqual(Path(auto_game_root(nested, None)), (library / 'common' / 'Transport Fever 2').resolve())

    def test_an_explicit_choice_wins_and_other_places_get_nothing(self):
        with tempfile.TemporaryDirectory() as td:
            library = Path(td) / 'steamapps'
            mod = library / 'workshop' / 'content' / '1066780' / '123'
            mod.mkdir(parents=True)
            self.assertIsNone(auto_game_root(mod, None))  # no install there
            (library / 'common' / 'Transport Fever 2' / 'res').mkdir(parents=True)
            self.assertEqual(auto_game_root(mod, 'X:/chosen'), 'X:/chosen')
            elsewhere = Path(td) / 'downloads' / 'mod'
            elsewhere.mkdir(parents=True)
            self.assertIsNone(auto_game_root(elsewhere, None))


if __name__ == '__main__':
    unittest.main()
