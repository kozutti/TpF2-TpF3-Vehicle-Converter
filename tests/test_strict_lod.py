"""Differential tests: native strict_lod (WASM) vs the pure-Python lod.simplify_mesh."""
import math
import os
import random
import unittest
from pathlib import Path
from unittest.mock import patch

from converter.parsers.lua import parse
from converter.transforms import lod, strict_lod
from converter.transforms.strict_lod import native_available, simplify_mesh_native


def _definition(with_uv1_in_sub2=False):
    specs = {'position': {'numComp': 3}, 'normal': {'numComp': 3}, 'tangent': {'numComp': 4},
             'uv0': {'numComp': 2}, 'uv1': {'numComp': 2}}
    names = list(specs)
    subs = {1: {'indices': {n: {} for n in names}},
            2: {'indices': {n: {} for n in names if with_uv1_in_sub2 or n != 'uv1'}}}
    return {'vertexAttr': specs, 'subMeshes': subs}


def _corner(rng, p, normal, u, v, noisy, nan=False):
    n = tuple(c + (rng.uniform(-1e-6, 1e-6) if noisy else 0.) for c in normal)
    t = (1. + (rng.uniform(-1e-6, 1e-6) if noisy else 0.),
         (rng.uniform(-1e-6, 1e-6) if noisy else 0.), 0., -1.)
    if nan:
        t = (math.nan, 0., 0., 1.)
    return {'normal': n, 'tangent': t, 'uv0': (u, v), 'uv1': (u * 2., v * .5)}


def _build(positions, tris, seed, split_subs=True, nan_tangent=True, noisy=True):
    """tris: list of (ids, per-corner (u,v)). Sub 2 (no uv1 stream) gets faces whose centroid z < 0."""
    rng = random.Random(seed)
    definition = _definition()
    faces = []
    for k, (ids, uvs) in enumerate(tris):
        sub = 1 if (not split_subs or sum(positions[i][2] for i in ids) >= 0) else 2
        nrm = lod._normal([positions[i] for i in ids])
        if nrm is None:
            continue
        attrs = []
        for j, i in enumerate(ids):
            a = _corner(rng, positions[i], nrm, uvs[j][0], uvs[j][1], noisy, nan=(nan_tangent and k == 7 and j == 1))
            names = definition['subMeshes'][sub]['indices']
            attrs.append({n: a[n] for n in names if n != 'position'})
        faces.append({'ids': list(ids), 'attrs': attrs, 'sub': sub - 1, 'normal': nrm})
    definition, blob, _ = lod._pack(definition, positions, faces)
    return definition, blob


def sphere_mesh(rings=24, around=32, seed=1):
    """Sphere-like closed-in-longitude tube (straight generators so collapses are possible) with a
    UV seam where u=0 / u=1 share positions, slightly bumpy radius and a tilted-cap tail."""
    rng = random.Random(seed)
    positions = []
    for r in range(rings + 1):
        z = -.5 + r / rings
        for a in range(around):
            th = 2 * math.pi * a / around
            rad = .5 + .0004 * math.sin(2 * z) + rng.uniform(-2e-5, 2e-5)
            positions.append((rad * math.cos(th), rad * math.sin(th), z))
    def vid(r, a):
        return r * around + a % around
    tris = []
    for r in range(rings):
        for a in range(around):
            u0, u1, v0, v1 = a / around, (a + 1) / around, r / rings, (r + 1) / rings
            q = [(vid(r, a), (u0, v0)), (vid(r, a + 1), (u1, v0)), (vid(r + 1, a + 1), (u1, v1)), (vid(r + 1, a), (u0, v1))]
            tris.append(([q[0][0], q[2][0], q[3][0]], [q[0][1], q[2][1], q[3][1]]))
            tris.append(([q[0][0], q[1][0], q[2][0]], [q[0][1], q[1][1], q[2][1]]))
    return _build(positions, tris, seed)


def open_grid_mesh(side=24, seed=2, bump=.003):
    """Open (boundary) bumpy grid, z straddles 0 so both submeshes occur."""
    rng = random.Random(seed)
    positions = [(x * .1, y * .1, bump * math.sin(x * .7) * math.cos(y * .5) + rng.uniform(-1e-4, 1e-4) - .0005)
                 for y in range(side + 1) for x in range(side + 1)]
    tris = []
    for y in range(side):
        for x in range(side):
            a = y * (side + 1) + x; b = a + 1; c = a + side + 1; d = c + 1
            uv = lambda i: (((i % (side + 1)) / side), ((i // (side + 1)) / side))
            tris.append(([a, b, d], [uv(a), uv(b), uv(d)]))
            tris.append(([a, d, c], [uv(a), uv(d), uv(c)]))
    return _build(positions, tris, seed)


def flat_grid_mesh(side=20, seed=3):
    """Perfectly planar quad grid with a UV seam down the middle (islands do not merge across it)."""
    positions = [(float(x), float(y), 0.) for y in range(side + 1) for x in range(side + 1)]
    tris = []
    half = side // 2
    for y in range(side):
        for x in range(side):
            a = y * (side + 1) + x; b = a + 1; c = a + side + 1; d = c + 1
            def uv(i, right):
                px, py = i % (side + 1), i // (side + 1)
                shift = .25 if (px >= half and not (right and px == half)) else 0.
                return (px / side + shift, py / side)
            left = x < half
            tris.append(([a, b, d], [uv(a, left), uv(b, left), uv(d, left)]))
            tris.append(([a, d, c], [uv(a, left), uv(d, left), uv(c, left)]))
    return _build(positions, tris, seed, split_subs=False, noisy=True)


def _assert_same(case, expected, actual):
    case.assertEqual(expected[0], actual[0], 'definition differs')
    case.assertEqual(expected[1], actual[1], 'blob differs')
    es, as_ = expected[2], actual[2]
    case.assertEqual(sorted(es), sorted(as_), 'stats key set differs')
    for key in es:
        case.assertEqual(es[key], as_[key], f'stats[{key!r}] differs')
        case.assertIs(type(es[key]), type(as_[key]), f'stats[{key!r}] type differs')
    case.assertEqual(es, as_)


class FallbackTests(unittest.TestCase):
    """Always run: without the wasm the wrapper must equal the reference."""

    def test_fallback_equals_reference(self):
        definition, blob = open_grid_mesh(8)
        budget = len(blob) // 2
        expected = lod.simplify_mesh(definition, blob, budget=budget)
        with patch.object(strict_lod, '_load', side_effect=strict_lod.NativeUnavailable('x')):
            actual = simplify_mesh_native(definition, blob, budget=budget)
        _assert_same(self, expected, actual)

    def test_early_return_when_under_budget(self):
        definition, blob = open_grid_mesh(4)
        expected = lod.simplify_mesh(definition, blob, budget=10**9)
        _assert_same(self, expected, simplify_mesh_native(definition, blob, budget=10**9))

    def test_missing_file_means_unavailable(self):
        with patch.object(strict_lod, '_module', None), patch.object(strict_lod, 'WASM_PATH', Path('does/not/exist.wasm')):
            self.assertFalse(native_available())


@unittest.skipUnless(native_available(), 'strict_lod.wasm not available')
class DifferentialTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.meshes = {
            'sphere': sphere_mesh(),
            'open_grid': open_grid_mesh(),
            'flat_seam': flat_grid_mesh(),
        }
        cls.base = {}
        for name, (definition, blob) in cls.meshes.items():
            positions, faces = lod._decode(definition, blob)
            cls.base[name] = lod._pack(definition, positions, faces, quantize_shading=True)[2]['budget_bytes']

    def _compare(self, name, budget_fraction=None, budget=None, max_work=None):
        definition, blob = self.meshes[name]
        b = budget if budget is not None else max(1, int(self.base[name] * budget_fraction))
        with self.subTest(mesh=name, budget=b, max_work=max_work):
            expected = lod.simplify_mesh(definition, blob, budget=b, max_work=max_work)
            actual = simplify_mesh_native(definition, blob, budget=b, max_work=max_work)
            if expected[2]['budget_bytes'] is not None and 'components_before' in expected[2]:
                self.assertEqual(strict_lod.LAST_NATIVE_COMPONENTS_BEFORE, expected[2]['components_before'])
            _assert_same(self, expected, actual)
        return expected

    def test_under_budget_early_return(self):
        self._compare('sphere', budget=10**9)

    def test_sphere_budgets(self):
        for frac in (.7, .4, .15):
            self._compare('sphere', frac)

    def test_open_grid_budgets(self):
        for frac in (.6, .25):
            self._compare('open_grid', frac)

    def test_flat_seam_grid(self):
        for frac in (.5, .2):
            self._compare('flat_seam', frac)

    def test_unreachable_budget(self):
        for name in ('sphere', 'open_grid'):
            stats = self._compare(name, budget=1)[2]
            self.assertFalse(stats['budget_met'])

    def test_max_work_limits(self):
        for name in ('sphere', 'open_grid'):
            for work in (1, 2000, 20000):
                self._compare(name, .3, max_work=work)

    def test_cancel(self):
        definition, blob = self.meshes['sphere']
        with self.assertRaises(InterruptedError):
            simplify_mesh_native(definition, blob, budget=1, cancel=lambda: True)
        calls = []
        def late():
            calls.append(1)
            return len(calls) > 2
        with self.assertRaises(InterruptedError):
            simplify_mesh_native(definition, blob, budget=1, cancel=late)


@unittest.skipUnless(native_available() and os.environ.get('STRICT_LOD_REAL_MESHES'),
                     'set STRICT_LOD_REAL_MESHES=<dir> (and optionally STRICT_LOD_REAL_LIMIT=n)')
class RealMeshTests(unittest.TestCase):
    def test_real_meshes_equal(self):
        root = Path(os.environ['STRICT_LOD_REAL_MESHES'])
        limit = int(os.environ.get('STRICT_LOD_REAL_LIMIT', '5'))
        done = 0
        for path in sorted(root.rglob('*.msh')):
            blob_path = Path(str(path) + '.blob')
            if not blob_path.is_file() or done >= limit:
                continue
            try:
                definition = parse(path.read_text(encoding='utf-8-sig'), str(path))
                blob = blob_path.read_bytes()
                lod._decode(definition, blob)
            except Exception:
                continue
            with self.subTest(mesh=str(path)):
                budget = max(1, min(lod.TARGET_BYTES, int(len(blob) * .5)))
                _assert_same(self, lod.simplify_mesh(definition, blob, budget=budget),
                             simplify_mesh_native(definition, blob, budget=budget))
            done += 1


if __name__ == '__main__':
    unittest.main()
