import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from converter.cli import main
from converter.gui.app import ConverterWindow, SINGLES_LABELS


class GameRootCliTests(unittest.TestCase):
    def test_texture_worker_option_is_forwarded_for_convert_and_batch(self):
        for command in ('convert', 'batch'):
            for workers in (1, 5, 8, 10, 32, 64):
                with self.subTest(command=command, workers=workers), patch('converter.pipeline.' + command, return_value={'status': 'success'}) as operation:
                    with contextlib.redirect_stdout(io.StringIO()):
                        self.assertEqual(main([command, '入力MOD', '--texture-workers', str(workers)]), 0)
                    self.assertEqual(operation.call_args.kwargs['texture_workers'], workers)

    def test_mod_worker_option_is_forwarded_for_batch_only(self):
        with patch('converter.pipeline.batch', return_value={'status': 'success'}) as operation:
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main(['batch', '入力MOD']), 0)
                self.assertEqual(operation.call_args.kwargs['mod_workers'], 1)
                self.assertEqual(main(['batch', '入力MOD', '--mod-workers', '4']), 0)
            self.assertEqual(operation.call_args.kwargs['mod_workers'], 4)
        for bad in ('0', '65'):
            with patch('converter.pipeline.batch') as operation, contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                main(['batch', '入力MOD', '--mod-workers', bad])
            operation.assert_not_called()
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            main(['convert', '入力MOD', '--mod-workers', '2'])

    def test_texture_worker_counts_above_sixty_four_are_rejected(self):
        for command in ('convert', 'batch'):
            with self.subTest(command=command), patch('converter.pipeline.' + command) as operation:
                with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
                    main([command, '入力MOD', '--texture-workers', '65'])
                self.assertEqual(error.exception.code, 2)
                operation.assert_not_called()

    def test_analyze_plan_convert_pass_explicit_installation_folder(self):
        for command in ('analyze', 'plan', 'convert'):
            with self.subTest(command=command), patch('converter.pipeline.' + command, return_value={'status': 'success'}) as operation:
                with contextlib.redirect_stdout(io.StringIO()):
                    code = main([command, '入力MOD', '--game-root', '本体フォルダ'])
                self.assertEqual(code, 0)
                self.assertEqual(operation.call_args.kwargs['game_root'], Path('本体フォルダ'))

    def test_omitting_folder_passes_none_and_validate_remains_independent(self):
        for command in ('analyze', 'plan', 'convert', 'validate'):
            with self.subTest(command=command), patch('converter.pipeline.' + command, return_value={'status': 'success'}) as operation:
                with contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(main([command, '入力MOD']), 0)
                if command == 'validate':
                    self.assertEqual(operation.call_args.kwargs, {})
                else:
                    self.assertIsNone(operation.call_args.kwargs['game_root'])


class GameRootGuiTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.base = Path(self.directory.name)
        self.setting = self.base / 'settings.json'
        self.setting_patch = patch('converter.gui.app.settings_path', return_value=self.setting)
        self.setting_patch.start()
        class Variable:
            def __init__(self, value='', **kwargs):
                self.value = value
            def get(self):
                return self.value
            def set(self, value):
                self.value = value
        self.ui_patches = [patch('converter.gui.app.tk.StringVar', Variable)]
        for module, names in (('tk', ('Label', 'Text')),
                              ('ttk', ('Frame', 'Label', 'Entry', 'Button', 'Combobox', 'Checkbutton', 'Progressbar', 'Scrollbar'))):
            self.ui_patches.extend(patch('converter.gui.app.' + module + '.' + name, MagicMock()) for name in names)
        for mocked_ui in self.ui_patches:
            mocked_ui.start()
        self.root = MagicMock()

    def tearDown(self):
        for mocked_ui in reversed(self.ui_patches):
            mocked_ui.stop()
        self.setting_patch.stop()
        self.directory.cleanup()

    def test_selector_saves_and_clear_removes_optional_setting(self):
        app = ConverterWindow(self.root, False, [])
        self.assertEqual(app.game_root.get(), '')
        with patch('converter.gui.app.filedialog.askdirectory', return_value=str(self.base)):
            app.choose_game_root()
        self.assertEqual(json.loads(self.setting.read_text(encoding='utf-8'))['game_root'], str(self.base))
        # A second window loads the saved choice without touching game files.
        other = ConverterWindow(self.root, False, [])
        self.assertEqual(other.game_root.get(), str(self.base))
        other.clear_game_root()
        self.assertEqual(json.loads(self.setting.read_text(encoding='utf-8'))['game_root'], '')

    def test_texture_worker_choice_is_saved_loaded_and_forwarded(self):
        app = ConverterWindow(self.root, False, [])
        self.assertEqual(app.texture_workers.get(), '8')
        app.texture_workers.set('64')
        app.save_settings()
        other = ConverterWindow(self.root, False, [])
        self.assertEqual(other.texture_workers.get(), '64')
        other.source.set(str(self.base))
        other.output.set(str(self.base / 'output'))
        for command in ('convert', 'batch'):
            with patch('converter.pipeline.' + command, return_value={'status': 'success'}) as operation:
                other.start(command)
                other.worker.join(timeout=5)
                self.assertEqual(operation.call_args.kwargs['texture_workers'], 64)
                other.finish()

    def test_mod_worker_choice_is_saved_loaded_and_forwarded_to_batch(self):
        app = ConverterWindow(self.root, False, [])
        self.assertEqual(app.mod_workers.get(), '1')
        app.mod_workers.set('3')
        app.save_settings()
        other = ConverterWindow(self.root, False, [])
        self.assertEqual(other.mod_workers.get(), '3')
        other.source.set(str(self.base))
        other.output.set(str(self.base / 'output'))
        with patch('converter.pipeline.batch', return_value={'status': 'success'}) as operation:
            other.start('batch')
            other.worker.join(timeout=5)
            self.assertEqual(operation.call_args.kwargs['mod_workers'], 3)
            other.finish()

    def test_worker_receives_saved_folder_and_validate_ignores_it(self):
        app = ConverterWindow(self.root, False, [])
        app.source.set(str(self.base))
        app.output.set(str(self.base / 'output'))
        app.game_root.set(str(self.base))
        for command in ('analyze', 'plan', 'convert', 'validate'):
            with self.subTest(command=command), patch('converter.pipeline.' + command, return_value={'status': 'success'}) as operation:
                app.start(command)
                app.worker.join(timeout=5)
                self.assertFalse(app.worker.is_alive())
                if command == 'validate':
                    self.assertEqual(operation.call_args.kwargs, {})
                else:
                    self.assertEqual(operation.call_args.kwargs['game_root'], self.base)
                app.finish()

    def test_batch_worker_receives_options_and_sibling_switch(self):
        app = ConverterWindow(self.root, False, [])
        app.source.set(str(self.base))
        app.output.set(str(self.base / 'output'))
        app.singles.set(SINGLES_LABELS['group'])
        for search, expected in (('1', True), ('0', False)):
            app.search_siblings.set(search)
            with patch('converter.pipeline.batch', return_value={'status': 'success'}) as operation:
                app.start('batch')
                app.worker.join(timeout=5)
                self.assertEqual(operation.call_args.args[:2], (self.base, self.base / 'output'))
                self.assertEqual(operation.call_args.kwargs['single_vehicles'], 'group')
                self.assertIs(operation.call_args.kwargs['search_siblings'], expected)
                app.finish()
        saved = json.loads(self.setting.read_text(encoding='utf-8'))
        self.assertEqual((saved['singles'], saved['search_siblings']), ('group', False))

    def test_parts_of_units_are_hidden_by_default_and_the_grouping_is_marked_experimental(self):
        """As in TPF2 the parts exist only inside the units; folding them into group rows is an experimental option."""
        app = ConverterWindow(self.root, False, [])
        self.assertEqual(app.singles_mode(), 'hide')
        self.assertIn('既定', SINGLES_LABELS['hide'])
        self.assertIn('実験', SINGLES_LABELS['group'])
        with self.assertRaises(SystemExit):
            with contextlib.redirect_stderr(io.StringIO()):
                main(['convert', '--singles', 'unknown'])

    def test_invalid_folder_is_rejected_before_worker_starts(self):
        app = ConverterWindow(self.root, False, [])
        app.source.set(str(self.base))
        app.game_root.set(str(self.base / 'missing'))
        with patch('converter.gui.app.messagebox.showerror') as error:
            app.start('analyze')
        self.assertIsNone(app.worker)
        error.assert_called_once()


class ResultsWindowTests(unittest.TestCase):
    def test_every_mod_is_listed_with_its_result_and_skipped_rows_can_be_hidden(self):
        import tkinter as tk
        from converter.gui.app import ResultsWindow
        try:
            root = tk.Tk()
        except tk.TclError as error:
            self.skipTest(f'no display: {error}')
        root.withdraw()
        try:
            summary = {'output': 'out', 'counts': {'skipped': 1, 'success': 1}, 'mods': [
                {'folder': '1', 'title': '車両', 'status': 'success', 'package_dir': 'p', 'diagnostics': {'error': 0},
                 'reference_mods': {'base': 3}, 'contents': ['道路車両 2']},
                {'folder': '2', 'title': 'スクリプト', 'status': 'skipped', 'reason': '対象外: スクリプト'}]}
            opened = []
            window = ResultsWindow(root, summary, opened.append)
            rows = [window.tree.item(i, 'values') for i in window.tree.get_children()]
            self.assertEqual([r[0] for r in rows], ['成功', '対象外'])
            self.assertIn('base', rows[0][3])
            self.assertIn('道路車両 2', rows[0][5])
            self.assertIn('スクリプト', rows[1][5])
            window.tree.selection_set('0')
            window.open_selected()
            self.assertEqual([str(p) for p in opened], ['p'])
            window.hide_skipped.set('1')
            window.fill()
            self.assertEqual(len(window.tree.get_children()), 1)
            window.destroy()
        finally:
            root.destroy()


if __name__ == '__main__':
    unittest.main()
