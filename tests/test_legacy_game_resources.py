from pathlib import Path
import tempfile
import unittest
import zipfile

from converter.adapters.game_assets import game_input
from converter.pipeline import convert
from test_conversion import fixture


class LegacyGameResourcesTests(unittest.TestCase):
    def test_installed_official_legacy_assets_are_collected_without_modifying_game_or_source(self):
        with tempfile.TemporaryDirectory() as td:
            root, game = Path(td) / 'mod', Path(td) / 'TPF2'
            fixture(root)
            (game / 'res').mkdir(parents=True)
            legacy = game / 'mods/urbangames_legacy_vehicle_pack_1'
            (legacy / 'res').mkdir(parents=True)
            meshes = list((root / 'res/models/mesh').rglob('*'))
            archive = legacy / 'res/old-meshes.zip'
            with zipfile.ZipFile(archive, 'w') as z:
                for path in meshes:
                    if path.is_file():
                        z.writestr(path.relative_to(root / 'res').as_posix(), path.read_bytes())
                        path.unlink()
            original = archive.read_bytes()
            before = {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob('*') if p.is_file()}
            result = convert(root, Path(td) / 'out', game_root=game, siblings=None, texture_workers=60)
            self.assertEqual(result['status'], 'success', result['diagnostics'])
            self.assertEqual(archive.read_bytes(), original)
            self.assertEqual(before, {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob('*') if p.is_file()})

    def test_explicit_base_and_mod_assets_win_over_legacy_pack_and_res_selection_is_supported(self):
        with tempfile.TemporaryDirectory() as td:
            root, game = Path(td) / 'mod', Path(td) / 'TPF2'
            fixture(root)
            (root / 'res/models/model/extra.mdl').write_text('return {mesh="priority.msh"}', encoding='utf-8')
            for directory, content in ((game, 'base'), (game / 'mods/urbangames_legacy_vehicle_pack_1', 'legacy')):
                path = directory / 'res/models/mesh/priority.msh'
                path.parent.mkdir(parents=True)
                path.write_text('return {provider="' + content + '"}', encoding='utf-8')
            with game_input(root, game / 'res') as (overlay, info):
                text = (overlay / 'res/models/mesh/priority.msh').read_text('utf-8')
                self.assertIn('base', text)
                self.assertEqual(len(info['reference_roots']), 2)
            local = root / 'res/models/mesh/priority.msh'
            local.write_text('return {provider="local"}', encoding='utf-8')
            with game_input(root, game) as (overlay, info):
                self.assertIn('local', (overlay / 'res/models/mesh/priority.msh').read_text('utf-8'))


if __name__ == '__main__':
    unittest.main()
