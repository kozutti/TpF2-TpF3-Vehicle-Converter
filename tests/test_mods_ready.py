"""Output folder names (`<id>_<vehicle name>`) and the flat `mods_ready` folder that goes straight into the game's mods folder."""
from pathlib import Path
import json
import tempfile
import unittest

from converter.batch import batch
from converter.naming import clean_title, mod_id_core, output_folder_name, safe_name, suggest_vehicle_name, unique_child
from converter.pipeline import convert

from test_conversion import fixture


class NamingTests(unittest.TestCase):
    def test_id_only_folder_gets_the_vehicle_name(self):
        self.assertEqual(output_folder_name(Path('x/9000000004'), 'Shinkansen 800'), '9000000004_800系新幹線')

    def test_folder_that_already_has_a_name_is_kept(self):
        self.assertEqual(output_folder_name(Path('x/9000000004_800系新幹線'), 'Shinkansen 800'), '9000000004_800系新幹線')
        self.assertEqual(output_folder_name(Path('x/200_addon'), 'Add-on'), '200_addon')

    def test_id_only_folder_without_a_usable_name_stays_the_id(self):
        self.assertEqual(output_folder_name(Path('x/9000000001'), '9000000001'), '9000000001')
        self.assertEqual(output_folder_name(Path('x/9000000001'), ''), '9000000001')

    def test_series_name_is_taken_from_the_title(self):
        cases = {'JR東日本E233系3000番台 Ver: 4.0.2': 'E233系3000番台', 'JREast Series215 EMU': '215系', 'JR Central 313 Series': '313系',
                 '京王帝都電鉄 8000系電車': '京王8000系', '小田急電鉄 4000形電車(2代目)': '小田急4000形', 'Nishitetsu 9000 Series': '西鉄9000系',
                 'Shinkansen E7': 'E7系新幹線', 'JR 新幹線E5系/E6系 --- shinkansen': 'E5／E6系新幹線', 'KEIKYU2100_JPTPF': '京急2100',
                 'JNR/JR 211/415/719系電車 - made by someone': '211／415／719系', '近畿日本鉄道 アーバンライナー - made by someone': '近鉄アーバンライナー',
                 'JR東日本 255系電車 - made by someone': '255系', '東京地下鉄/東京メトロ16000系 3次車 Ver1.1.0': '東京メトロ16000系'}
        for title, expected in cases.items():
            self.assertEqual(suggest_vehicle_name(title), expected, title)
        self.assertEqual(suggest_vehicle_name(''), '')

    def test_foreign_titles_are_not_given_a_japanese_series(self):
        for title in ('British Rail Class 47', 'VT18.16 / VT175', 'S-Bahnen BR 423/422/430', 'Intercity 225', 'TRA EMU500'):
            self.assertEqual(suggest_vehicle_name(title), '', title)
        self.assertEqual(output_folder_name(Path('x/9000000002'), 'British Rail Class 47'), '9000000002_British Rail Class 47')

    def test_title_without_a_series_falls_back_to_the_cleaned_title(self):
        self.assertEqual(output_folder_name(Path('x/9000000003'), 'Cargo Asset Set'), '9000000003_Cargo Asset Set')

    def test_version_and_credit_suffixes_are_dropped(self):
        self.assertEqual(clean_title('JR東日本E233系3000番台 Ver: 4.0.2'), 'JR東日本E233系3000番台')
        self.assertEqual(clean_title('JR東日本 E259/E353 - made by someone   ---   tag'), 'JR東日本 E259／E353')
        self.assertEqual(clean_title('Class 156 Reworked'), 'Class 156 Reworked')

    def test_characters_windows_forbids_become_full_width(self):
        self.assertEqual(safe_name('キハ40/47: "A"?'), 'キハ40／47： ”A”？')
        self.assertEqual(safe_name('name. '), 'name')

    def test_name_is_capped(self):
        self.assertLessEqual(len(clean_title('あ' * 200)), 60)

    def test_mod_id_uses_the_workshop_id_only(self):
        self.assertEqual(mod_id_core(Path('x/9000000004')), '9000000004')
        self.assertEqual(mod_id_core(Path('x/9000000004_800系新幹線')), '9000000004')
        self.assertEqual(mod_id_core(Path('x/200_addon')), '200_addon')

    def test_unique_child_never_reuses_a_folder(self):
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / 'a').mkdir()
            self.assertEqual(unique_child(td, 'a').name, 'a_2')
            self.assertEqual(unique_child(td, 'b').name, 'b')


class ModsReadyTests(unittest.TestCase):
    def library(self, td):
        library = Path(td) / 'workshop'
        library.mkdir()
        fixture(library / '9000000004')
        fixture(library / '9000000006_215系')
        (library / '9000000004' / 'mod.lua').write_text('function data() return {info={name="800系 Ver: 1.2.3"}} end', 'utf-8')
        return library

    def test_batch_puts_every_package_flat_in_mods_ready(self):
        with tempfile.TemporaryDirectory() as td:
            result = batch(self.library(td), Path(td) / 'out')
            run = Path(result['run_dir'])
            ready = run / 'mods_ready'
            self.assertEqual(sorted(p.name for p in ready.iterdir()), ['9000000004_800系', '9000000006_215系'])
            for folder in ready.iterdir():
                self.assertTrue((folder / 'mod.json').is_file() and (folder / 'content').is_dir() and (folder / '_metadata').is_dir())
            rows = {r['folder']: r for r in result['batch']['mods']}
            self.assertEqual(rows['9000000004']['package_dir'], str(ready / '9000000004_800系'))
            self.assertEqual(result['batch']['mods_ready'], str(ready))
            self.assertIn(str(ready), (run / 'batch_summary.md').read_text('utf-8'))

    def test_modid_has_no_hash_and_is_independent_of_the_folder_name(self):
        with tempfile.TemporaryDirectory() as td:
            result = batch(self.library(td), Path(td) / 'out')
            ids = {Path(r['package_dir']).name: json.loads((Path(r['package_dir']) / 'mod.json').read_text('utf-8-sig'))['modId']
                   for r in result['batch']['mods']}
            self.assertEqual(ids, {'9000000004_800系': 'converted_9000000004', '9000000006_215系': 'converted_9000000006'})

    def test_run_folder_keeps_reports_but_no_package_and_the_manifest_still_lists_it(self):
        with tempfile.TemporaryDirectory() as td:
            result = batch(self.library(td), Path(td) / 'out')
            row = next(r for r in result['batch']['mods'] if r['folder'] == '9000000004')
            run = Path(row['run_dir'])
            self.assertTrue((run / 'reports' / 'conversion_report.md').is_file())
            self.assertFalse((run / 'package').exists())
            manifest = json.loads((run / 'reports' / 'manifest.json').read_text('utf-8'))
            self.assertIn('package/converted_9000000004/mod.json', manifest['output'])

    def test_single_conversion_is_unchanged_without_mods_dir(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / '9000000004'
            fixture(root)
            result = convert(root, Path(td) / 'out')
            self.assertEqual(Path(result['package_dir']).name, 'converted_9000000004')
            self.assertEqual(Path(result['package_dir']).parent.name, 'package')

    def test_single_conversion_with_mods_dir_moves_the_package(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / '9000000004'
            fixture(root)
            ready = Path(td) / 'out' / 'mods_ready'
            result = convert(root, Path(td) / 'out', mods_dir=ready)
            self.assertEqual(result['status'], 'success')
            self.assertEqual(Path(result['package_dir']).parent, ready)
            self.assertTrue((Path(result['package_dir']) / 'mod.json').is_file())
            self.assertFalse((Path(result['run_dir']) / 'package').exists())


if __name__ == '__main__':
    unittest.main()
