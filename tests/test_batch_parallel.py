"""Batch conversion with several MODs at the same time (one process per MOD)."""
from hashlib import sha256
from pathlib import Path
import tempfile
import threading
import unittest

from converter.batch import MAX_MOD_WORKERS, batch, pool_sizes, unique_mods_names, validate_mod_workers

from test_conversion import fixture


def tree_hashes(folder):
    return {p.relative_to(folder).as_posix(): sha256(p.read_bytes()).hexdigest() for p in sorted(Path(folder).rglob('*')) if p.is_file()}


class ParallelBatchTests(unittest.TestCase):
    def library(self, td):
        library = Path(td) / 'workshop'
        library.mkdir()
        for name in ('9000000004', '9000000006_215系', '2300000001'):
            fixture(library / name)
        (library / '9000000004' / 'mod.lua').write_text('function data() return {info={name="800系 Ver: 1.2.3"}} end', 'utf-8')
        script = library / '2400000002'  # a MOD without rail vehicles is reported as not applicable
        script.mkdir()
        (script / 'mod.lua').write_text('function data() return {info={name="Script"}} end', 'utf-8')
        return library

    def test_parallel_result_matches_the_sequential_one(self):
        with tempfile.TemporaryDirectory() as td:
            library = self.library(td)
            sequential = batch(library, Path(td) / 'seq')
            parallel = batch(library, Path(td) / 'par', mod_workers=3)
            self.assertEqual(parallel['batch']['mod_workers'], 3)
            self.assertEqual(sequential['batch']['mod_workers'], 1)
            seq_rows, par_rows = sequential['batch']['mods'], parallel['batch']['mods']
            self.assertEqual([r['folder'] for r in par_rows], [r['folder'] for r in seq_rows])
            self.assertEqual([r['status'] for r in par_rows], [r['status'] for r in seq_rows])
            self.assertEqual(parallel['batch']['counts'], sequential['batch']['counts'])
            self.assertEqual(parallel['status'], sequential['status'])
            seq_ready, par_ready = Path(sequential['batch']['mods_ready']), Path(parallel['batch']['mods_ready'])
            self.assertEqual(sorted(p.name for p in par_ready.iterdir()), sorted(p.name for p in seq_ready.iterdir()))
            self.assertEqual(len(list(par_ready.iterdir())), 3)
            self.assertEqual(tree_hashes(par_ready), tree_hashes(seq_ready))
            skipped = next(r for r in par_rows if r['folder'] == '2400000002')
            self.assertEqual(skipped['status'], 'skipped')
            self.assertIn('MODの並列数: 3', (Path(parallel['run_dir']) / 'batch_summary.md').read_text('utf-8'))

    def test_progress_comes_from_the_parent_with_the_mod_name(self):
        with tempfile.TemporaryDirectory() as td:
            events = []
            batch(self.library(td), Path(td) / 'out', mod_workers=2, progress=events.append)
            started = [e for e in events if e['stage'] == 'batch' and e['current']]
            self.assertEqual(len(started), 4)
            self.assertEqual(sorted(e['current'] for e in started), [1, 2, 3, 4])
            self.assertTrue(all(e['total'] == 4 for e in started))
            others = [e for e in events if e['stage'] != 'batch' and e.get('message')]
            self.assertTrue(others)
            self.assertTrue(all(e['message'].startswith('[') for e in others))

    def test_many_processes_are_announced_but_not_limited(self):
        import os
        with tempfile.TemporaryDirectory() as td:
            events, library = [], self.library(td)
            result = batch(library, Path(td) / 'out', mod_workers=2, texture_workers=(os.cpu_count() or 1) + 1,
                           progress=events.append)
            notices = [e['message'] for e in events if e['stage'] == 'batch' and not e['current']]
            self.assertEqual(len(notices), 1)
            self.assertIn('注意', notices[0])
            self.assertEqual(result['batch']['texture_workers'], (os.cpu_count() or 1) + 1)
            quiet = []
            batch(library, Path(td) / 'out2', mod_workers=2, texture_workers=1, progress=quiet.append)
            self.assertFalse([e for e in quiet if e['stage'] == 'batch' and not e['current']])

    def test_cancel_before_start_converts_nothing(self):
        with tempfile.TemporaryDirectory() as td:
            cancel = threading.Event()
            cancel.set()
            result = batch(self.library(td), Path(td) / 'out', mod_workers=2, cancel=cancel)
            self.assertEqual(result['status'], 'cancelled')
            self.assertEqual(result['batch']['mods'], [])

    def test_cancel_while_running_stops_the_run(self):
        with tempfile.TemporaryDirectory() as td:
            cancel = threading.Event()
            result = batch(self.library(td), Path(td) / 'out', mod_workers=2, cancel=cancel,
                           progress=lambda event: cancel.set())
            self.assertEqual(result['status'], 'cancelled')
            self.assertLess(len(result['batch']['mods']), 4)

    def test_single_mod_runs_in_process(self):
        with tempfile.TemporaryDirectory() as td:
            library = Path(td) / 'workshop'
            library.mkdir()
            fixture(library / '9000000004')
            result = batch(library, Path(td) / 'out', mod_workers=4)
            self.assertEqual(result['batch']['mod_workers'], 1)
            self.assertEqual(result['batch']['counts'], {'success': 1})

    def test_names_are_distinct_even_when_they_differ_only_in_case(self):
        mods = [Path('a/Same'), Path('b/same'), Path('c/other')]
        names = unique_mods_names(mods, ['', '', ''])
        self.assertEqual(len({n.casefold() for n in names}), 3)
        self.assertEqual(names[0], 'Same')
        self.assertEqual(names[2], 'other')

    def test_up_to_sixty_four_workers_are_allowed_in_at_most_two_pools(self):
        self.assertEqual(MAX_MOD_WORKERS, 64)
        self.assertEqual(pool_sizes(61), [61])
        self.assertEqual(pool_sizes(62), [31, 31])
        self.assertEqual(pool_sizes(64), [32, 32])
        with tempfile.TemporaryDirectory() as td:  # the pool never grows beyond the number of MODs
            result = batch(self.library(td), Path(td) / 'out', mod_workers=64, texture_workers=1)
            self.assertEqual(result['batch']['mod_workers'], 4)

    def test_worker_count_is_validated(self):
        for bad in (0, MAX_MOD_WORKERS + 1, -1, 2.0, '2', True):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                validate_mod_workers(bad)
        validate_mod_workers(1)
        validate_mod_workers(MAX_MOD_WORKERS)
        with tempfile.TemporaryDirectory() as td, self.assertRaises(ValueError):
            batch(self.library(td), Path(td) / 'out', mod_workers=0)


if __name__ == '__main__':
    unittest.main()
