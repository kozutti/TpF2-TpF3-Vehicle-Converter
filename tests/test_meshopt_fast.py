"""Differential tests: meshopt_fast (native trial evaluator) vs meshopt._simplify_meshopt_reference."""
import math
import os
import random
import unittest
from pathlib import Path
from unittest.mock import patch

from converter.transforms import lod, meshopt, meshopt_fast, strict_lod
from converter.transforms.strict_lod import native_available
from tests.test_strict_lod import sphere_mesh, open_grid_mesh, flat_grid_mesh, _build

NATIVE = native_available()


def loose_pieces_mesh(count=60, seed=5):
    """Many tiny components: loose triangles and quads (<= 12 corner indices each) plus noisy tangents."""
    rng = random.Random(seed)
    positions, tris = [], []
    for k in range(count):
        ox, oy, oz = rng.uniform(-5, 5), rng.uniform(-5, 5), rng.uniform(-1, 1)
        base = len(positions)
        if k % 3 == 0:
            positions += [(ox, oy, oz), (ox + .3, oy, oz + .01), (ox, oy + .3, oz)]
            tris.append(([base, base + 1, base + 2], [(0, 0), (1, 0), (0, 1)]))
        else:
            positions += [(ox, oy, oz), (ox + .3, oy, oz), (ox + .3, oy + .3, oz + .02), (ox, oy + .3, oz)]
            tris.append(([base, base + 1, base + 2], [(0, 0), (1, 0), (1, 1)]))
            tris.append(([base, base + 2, base + 3], [(0, 0), (1, 1), (0, 1)]))
    return _build(positions, tris, seed)


def mixed_mesh(seed=7):
    """One big tube (simplified) plus lots of loose bits in a single mesh."""
    big_def, big_blob = sphere_mesh(10, 16, seed)
    _, faces = lod._decode(big_def, big_blob)
    positions, _ = lod._decode(big_def, big_blob)
    positions = list(positions)
    tris = []
    for f in faces:
        tris.append((f['ids'], [(a['uv0'][0], a['uv0'][1]) for a in f['attrs']]))
    rng = random.Random(seed)
    for k in range(25):
        ox, oy = rng.uniform(2, 6), rng.uniform(2, 6)
        base = len(positions)
        positions += [(ox, oy, 0.), (ox + .2, oy, .0), (ox, oy + .2, .01)]
        tris.append(([base, base + 1, base + 2], [(0, 0), (1, 0), (0, 1)]))
    return _build(positions, tris, seed)


def sliver_mesh(seed=9):
    """Thin near-collinear strips (>12 indices): tries to make whole components collapse to degenerate."""
    positions, tris = [], []
    for k in range(4):
        base = len(positions)
        n = 12
        for i in range(n):
            positions.append((i * 1e-4, k * 3.0, 0.))
            positions.append((i * 1e-4, k * 3.0 + 1e-9, 0.))
        for i in range(n - 1):
            a, b, c, d = base + 2 * i, base + 2 * i + 1, base + 2 * i + 2, base + 2 * i + 3
            tris.append(([a, c, b], [(0, 0), (1, 0), (0, 1)]))
            tris.append(([b, c, d], [(0, 1), (1, 0), (1, 1)]))
    return _build(positions, tris, seed, nan_tangent=False)


MESHES = {
    'sphere': lambda: sphere_mesh(20, 28),
    'open_grid': lambda: open_grid_mesh(24),
    'flat_seam': lambda: flat_grid_mesh(24),
    'loose_pieces': lambda: loose_pieces_mesh(),
    'mixed': lambda: mixed_mesh(),
    'sliver': lambda: sliver_mesh(),
}


def assert_same(case, expected, actual):
    case.assertEqual(expected[0], actual[0], 'definition differs')
    case.assertEqual(expected[1], actual[1], 'blob differs')
    es, as_ = expected[2], actual[2]
    case.assertEqual(sorted(es), sorted(as_), 'stats key set differs')
    for key in es:
        case.assertEqual(es[key], as_[key], f'stats[{key!r}] differs')
    case.assertEqual(expected, actual)


@unittest.skipUnless(NATIVE, 'strict_lod.wasm not available')
class DifferentialTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.meshes = {name: build() for name, build in MESHES.items()}

    def test_differential(self):
        for name, (definition, blob) in self.meshes.items():
            n = len(blob)
            budgets = {'large': n * 10, 'mid': int(n * .45), 'low': int(n * .2), 'unreachable': 1}
            for label, budget in budgets.items():
                with self.subTest(mesh=name, budget=label):
                    expected = meshopt._simplify_meshopt_reference(definition, blob, budget)
                    actual = meshopt_fast.simplify_meshopt_fast(definition, blob, budget)
                    self.assertIsNotNone(actual, 'native path returned None although available')
                    assert_same(self, expected, actual)

    def test_loose_pieces_constant_path_used(self):
        definition, blob = self.meshes['loose_pieces']
        _, faces = lod._decode(definition, blob)
        groups = meshopt.components(faces)
        self.assertGreater(len(groups), 20)
        self.assertTrue(all(len(g) * 3 <= 12 for g in groups))

    def test_mismatch_falls_back(self):
        definition, blob = self.meshes['open_grid']
        budget = int(len(blob) * .45)
        expected = meshopt._simplify_meshopt_reference(definition, blob, budget)
        real = meshopt_fast._pack

        def wrong(*a, **k):
            packed, data, stats = real(*a, **k)
            return packed, data + b'\0', stats
        with patch.object(meshopt_fast, '_pack', wrong):
            with self.assertRaises(meshopt_fast.NativeMismatch):
                meshopt_fast.simplify_meshopt_fast(definition, blob, budget)
            assert_same(self, expected, meshopt.simplify_meshopt(definition, blob, budget))


class DispatcherTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.definition, cls.blob = open_grid_mesh(10)
        cls.budget = int(len(cls.blob) * .45)
        cls.expected = meshopt._simplify_meshopt_reference(cls.definition, cls.blob, cls.budget)

    def test_env_disables_native(self):
        with patch.dict(os.environ, {'TPF3_NO_NATIVE_PACK': '1'}), \
                patch.object(meshopt_fast, 'simplify_meshopt_fast', side_effect=AssertionError('native used')):
            assert_same(self, self.expected, meshopt.simplify_meshopt(self.definition, self.blob, self.budget))

    def test_native_unavailable_load(self):
        with patch.object(strict_lod, '_module', None), \
                patch.object(strict_lod, '_load', side_effect=strict_lod.NativeUnavailable('x')):
            assert_same(self, self.expected, meshopt.simplify_meshopt(self.definition, self.blob, self.budget))

    def test_native_unavailable_missing_wasm(self):
        with patch.object(strict_lod, '_module', None), \
                patch.object(strict_lod, 'WASM_PATH', Path('does/not/exist.wasm')):
            assert_same(self, self.expected, meshopt.simplify_meshopt(self.definition, self.blob, self.budget))

    def test_dispatcher_default(self):
        env = {k: v for k, v in os.environ.items() if k != 'TPF3_NO_NATIVE_PACK'}
        with patch.dict(os.environ, env, clear=True):
            assert_same(self, self.expected, meshopt.simplify_meshopt(self.definition, self.blob, self.budget))

    def test_cancel(self):
        with self.assertRaises(InterruptedError):
            meshopt.simplify_meshopt(self.definition, self.blob, self.budget, cancel=lambda: True)
        with patch.dict(os.environ, {'TPF3_NO_NATIVE_PACK': '1'}):
            with self.assertRaises(InterruptedError):
                meshopt.simplify_meshopt(self.definition, self.blob, self.budget, cancel=lambda: True)


@unittest.skipUnless(os.environ.get('STRICT_LOD_REAL_MESHES'), 'set STRICT_LOD_REAL_MESHES=<dir> to enable')
@unittest.skipUnless(NATIVE, 'strict_lod.wasm not available')
class RealMeshTests(unittest.TestCase):
    def test_real_meshes(self):
        from converter.parsers.lua import parse
        root = Path(os.environ['STRICT_LOD_REAL_MESHES'])
        limit = int(os.environ.get('STRICT_LOD_REAL_LIMIT', '3'))
        found = []
        for msh in root.rglob('*.msh'):
            blob = msh.with_name(msh.name + '.blob')
            if blob.is_file():
                found.append((blob.stat().st_size, msh, blob))
        found.sort(key=lambda t: t[0])
        if not found:
            self.skipTest('no .msh with sibling .msh.blob found')
        for _, msh, blob_path in found[:limit]:
            with self.subTest(mesh=str(msh)):
                definition = parse(msh.read_text(encoding='utf-8-sig'), str(msh))
                blob = blob_path.read_bytes()
                expected = meshopt._simplify_meshopt_reference(definition, blob, lod.TARGET_BYTES)
                actual = meshopt_fast.simplify_meshopt_fast(definition, blob, lod.TARGET_BYTES)
                self.assertIsNotNone(actual)
                assert_same(self, expected, actual)


if __name__ == '__main__':
    unittest.main()
