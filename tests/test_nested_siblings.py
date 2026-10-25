from pathlib import Path
import shutil
import tempfile
import unittest

from converter.adapters.game_assets import SiblingIndex
from converter.pipeline import convert, resolve_siblings
from test_conversion import fixture


class NestedSiblingTests(unittest.TestCase):
    def test_nested_workshop_mod_borrows_root_assets_without_changing_either_mod(self):
        with tempfile.TemporaryDirectory() as td:
            workshop = Path(td) / 'steamapps/workshop/content/1066780'
            source = workshop / 'group/variant'
            fixture(source)
            provider = workshop / 'base'
            for path in list((source / 'res/models/mesh').rglob('*')):
                if path.is_file():
                    destination = provider / path.relative_to(source)
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(path), str(destination))
            before = {p.relative_to(workshop).as_posix(): p.read_bytes() for p in workshop.rglob('*') if p.is_file()}
            index = resolve_siblings(source)
            self.assertEqual(index.folder, workshop)
            result = convert(source, Path(td) / 'out', texture_workers=60)
            self.assertEqual(result['status'], 'success', result['diagnostics'])
            self.assertEqual(before, {p.relative_to(workshop).as_posix(): p.read_bytes() for p in workshop.rglob('*') if p.is_file()})

    def test_root_index_finds_nested_provider_and_direct_provider_has_priority(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            relative = 'res/models/mesh/shared.msh'
            for directory in (root / 'z_base', root / 'group/a_nested'):
                path = directory / relative
                path.parent.mkdir(parents=True)
                path.write_text('return {}', encoding='utf-8')
            index = SiblingIndex(root)
            self.assertEqual(index.locate('shared.msh', 'res/models/model/unit.mdl'), root / 'z_base')
            self.assertEqual(index.locate('shared.msh', 'res/models/model/unit.mdl', exclude=root / 'z_base'), root / 'group/a_nested')

    def test_generic_folder_search_does_not_expand_to_arbitrary_ancestors(self):
        with tempfile.TemporaryDirectory() as td:
            source = Path(td) / 'group/mod'
            fixture(source)
            (Path(td) / 'outside/res').mkdir(parents=True)
            self.assertIsNone(resolve_siblings(source))
            self.assertIsNone(resolve_siblings(source, siblings=None))


if __name__ == '__main__':
    unittest.main()
