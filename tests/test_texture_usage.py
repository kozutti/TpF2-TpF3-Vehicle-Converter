import hashlib
from pathlib import Path
import struct
import tempfile
import unittest
import multiprocessing
import threading
import os
import time
from unittest.mock import patch

from converter.parsers.lua import parse, dumps
from converter.transforms.texture_usage import convert_package_textures, validate_texture_uses

def dxt1():
    """A 4x4 DXT1 with its whole mip chain (4x4, 2x2, 1x1: one block each); the converter completes a single-level chain."""
    header = [124, 0xA1007, 4, 4, 8, 0, 3] + [0] * 11
    header += [32, 4, struct.unpack('<I', b'DXT1')[0], 0, 0, 0, 0, 0]
    header += [0x401008, 0, 0, 0, 0]
    return b'DDS ' + struct.pack('<31I', *header) + struct.pack('<HHI', 0x841f, 0x841f, 0) * 3


def probe_texture_job(job):
    """Hold all 64 real processes at a barrier to prove simultaneous workers."""
    from converter.transforms import texture_usage
    markers = job[0].parent / 'workers_ready'
    (markers / str(os.getpid())).write_text('ready', 'utf-8')
    deadline = time.monotonic() + 40
    while len(list(markers.iterdir())) < 64:
        if texture_usage._worker_cancel.is_set():
            raise InterruptedError('test cancelled')
        if time.monotonic() >= deadline:
            raise TimeoutError('64 workers did not reach the barrier')
        time.sleep(0.05)
    result = texture_usage._convert_texture(job, texture_usage._worker_cancel)
    result['pid'] = os.getpid()
    return result

class TextureUsageTests(unittest.TestCase):
    def test_shared_normal_and_opacity_have_distinct_variants(self):
        from converter.transforms.textures import inspect_dds
        with tempfile.TemporaryDirectory() as td:
            package = Path(td)
            content = package / 'content'
            content.mkdir()
            source = content / 'shared.dds'
            source.write_bytes(dxt1())
            before = hashlib.sha256(source.read_bytes()).digest()
            material = {'type': 'PHYS_TRANSPARENT_NRML_MAP', 'params': {
                'map_normal': {'fragmentSamplers': {'normalTex': {'fileName': '/shared.dds'}}},
                'map_albedo_opacity': {'fragmentSamplers': {'albedoOpacityTex': {'fileName': '/shared.dds'}}}}}
            file = content / 'glass.mtl'
            file.write_text(dumps(material), encoding='utf-8')
            old_diagnostics = validate_texture_uses(content, {'glass.mtl': material})
            self.assertIn('TEXTURE_FORMAT_INVALID', {d['code'] for d in old_diagnostics})
            result = convert_package_textures(package)
            self.assertFalse(any(d['severity'] == 'error' for d in result), result)
            value = parse(file.read_text('utf-8'))
            normal = value['params']['map_normal']['fragmentSamplers']['normalTex']['fileName']
            opacity = value['params']['map_albedo_opacity']['fragmentSamplers']['albedoOpacityTex']['fileName']
            self.assertNotEqual(normal, opacity)
            self.assertEqual(inspect_dds(content / normal.lstrip('/'))['format'], 'BC5')
            self.assertEqual(inspect_dds(content / opacity.lstrip('/'))['format'], 'BC1')
            self.assertEqual(hashlib.sha256(source.read_bytes()).digest(), before)
            self.assertFalse(validate_texture_uses(content, {'glass.mtl': value}))

    def make_package(self, package, count=12):
        content = package / 'content'
        content.mkdir(parents=True)
        for i in range(count):
            (content / f'normal{i}.dds').write_bytes(dxt1())
            material = {'type': 'PHYS_NORMAL_MAP', 'params': {
                'map_normal': {'fileName': f'/normal{i}.dds'},
                'map_albedo_opacity': {'fileName': f'/normal{i}.dds'}}}
            (content / f'mat{i:02}.mtl').write_text(dumps(material), 'utf-8')
        # A second material shares both uses; each must resolve to the same asset.
        (content / 'shared.mtl').write_text((content / 'mat00.mtl').read_text('utf-8'), 'utf-8')
        return content

    def files(self, content):
        return {p.relative_to(content).as_posix(): p.read_bytes() for p in content.rglob('*') if p.is_file()}

    def test_five_eight_ten_workers_match_serial_bytes_and_diagnostic_order(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            serial = self.make_package(root / 'serial')
            expected = convert_package_textures(serial.parent, workers=1)
            expected_files = self.files(serial)
            self.assertEqual(sum(d['code'] == 'TEXTURE_FORMAT_CONVERTED' for d in expected), 24)
            for workers in (5, 8, 10, 32, 64):
                with self.subTest(workers=workers):
                    content = self.make_package(root / str(workers))
                    actual = convert_package_textures(content.parent, workers=workers)
                    self.assertEqual(self.files(content), expected_files)
                    self.assertEqual(actual, expected)

    def test_invalid_dds_does_not_stop_other_workers_or_rewrite_failed_use(self):
        with tempfile.TemporaryDirectory() as td:
            content = self.make_package(Path(td) / 'package', count=3)
            (content / 'normal1.dds').write_bytes(b'broken DDS')
            result = convert_package_textures(content.parent, workers=5)
            codes = [d['code'] for d in result]
            self.assertEqual(codes.count('TEXTURE_FORMAT_CONVERTED'), 4)
            failed = parse((content / 'mat01.mtl').read_text('utf-8'))
            self.assertEqual(failed['params']['map_normal']['fileName'], '/normal1.dds')
            ok = parse((content / 'mat00.mtl').read_text('utf-8'))
            self.assertIn('/converted_textures/', ok['params']['map_normal']['fileName'])
            self.assertTrue(any(d['severity'] == 'error' for d in result))

    def test_parallel_conversion_from_gui_style_thread(self):
        with tempfile.TemporaryDirectory() as td:
            content = self.make_package(Path(td) / 'package', count=3)
            outcomes = []
            def run():
                try:
                    outcomes.append(convert_package_textures(content.parent, workers=5))
                except BaseException as error:
                    outcomes.append(error)
            thread = threading.Thread(target=run)
            thread.start()
            thread.join(timeout=20)
            self.assertFalse(thread.is_alive())
            self.assertIsInstance(outcomes[0], list, outcomes)
            self.assertFalse(any(d['severity'] == 'error' for d in outcomes[0]))

    def test_cancellation_joins_workers_and_leaves_materials_unedited(self):
        from test_uncompressed_dds import raw_dds, normal_pixel
        with tempfile.TemporaryDirectory() as td:
            content = self.make_package(Path(td) / 'package', count=40)
            raw = raw_dds(512, 512, 1, normal_pixel)
            for i in range(1, 40):
                (content / f'normal{i}.dds').write_bytes(raw)
            before = {p.name: p.read_bytes() for p in content.glob('*.mtl')}
            existing = {p.pid for p in multiprocessing.active_children()}
            cancel = threading.Event()
            progress = []
            def on_progress(event):
                progress.append(event)
                if event['current'] > 0:
                    cancel.set()
            for workers in (5, 32, 64):
                with self.subTest(workers=workers):
                    cancel.clear()
                    with self.assertRaises(InterruptedError):
                        convert_package_textures(content.parent, on_progress, cancel, workers=workers)
                    self.assertTrue(cancel.is_set())
                    self.assertEqual({p.name: p.read_bytes() for p in content.glob('*.mtl')}, before)
                    self.assertEqual({p.pid for p in multiprocessing.active_children()}, existing)

    def test_invalid_worker_counts_are_rejected_and_precancel_creates_no_outputs(self):
        with tempfile.TemporaryDirectory() as td:
            content = self.make_package(Path(td) / 'package', count=2)
            for value in (0, 65, True, '8'):
                with self.assertRaises(ValueError):
                    convert_package_textures(content.parent, workers=value)
            cancel = threading.Event()
            cancel.set()
            with self.assertRaises(InterruptedError):
                convert_package_textures(content.parent, cancel=cancel, workers=8)
            self.assertFalse((content / '_assets').exists())

    def test_sixty_four_actual_workers_produce_serial_bytes_and_exit(self):
        from converter.transforms.texture_usage import _convert_jobs, _convert_texture
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / 'workers_ready').mkdir()
            source = root / 'source.dds'
            source.write_bytes(dxt1())
            expected = root / 'serial.dds'
            self.assertTrue(_convert_texture((source, expected, 'normal'))['ok'])
            jobs = [(source, root / f'out{i}.dds', 'normal') for i in range(64)]
            existing = {p.pid for p in multiprocessing.active_children()}
            with patch('converter.transforms.texture_usage._run_texture_job', probe_texture_job):
                results = _convert_jobs(jobs, 64, None, None)
            self.assertEqual(len({r['pid'] for r in results}), 64)
            self.assertTrue(all(r['ok'] for r in results))
            self.assertTrue(all(job[1].read_bytes() == expected.read_bytes() for job in jobs))
            self.assertEqual({p.pid for p in multiprocessing.active_children()}, existing)
