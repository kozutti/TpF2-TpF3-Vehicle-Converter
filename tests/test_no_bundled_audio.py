"""The converter never ships or copies third-party audio on its own.

Audio reaches an output package only when the user points the tool at their own
TPF2 installation (--game-root); that path is covered by test_game_assets.
"""
from pathlib import Path
import tempfile
import unittest

import converter
from converter.parsers.lua import dumps, parse
from converter.pipeline import convert

from test_conversion import fixture

AUDIO = ('.wav', '.ogg', '.mp3', '.flac', '.opus')


def audio_files(root):
    return sorted(p.relative_to(root).as_posix() for p in Path(root).rglob('*')
                  if p.is_file() and p.suffix.lower() in AUDIO)


class NoBundledAudioTests(unittest.TestCase):
    def test_converter_sources_and_tools_contain_no_audio(self):
        project = Path(converter.__file__).resolve().parents[1]
        for folder in ('converter', 'tools', 'tests', 'docs', 'LICENSES'):
            self.assertEqual(audio_files(project / folder), [], folder)

    def test_conversion_without_game_root_writes_no_audio_and_names_only_game_sounds(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / 'mod'
            model = fixture(root)
            model['metadata']['railVehicle']['soundSet'] = {'name': 'train_electric_modern'}
            (root / 'res/models/model/vehicle/train/unit.mdl').write_text(dumps(model), 'utf-8')
            result = convert(root, Path(td) / 'out')
            self.assertEqual(result['status'], 'success', result['diagnostics'])
            package = Path(result['package_dir'])
            self.assertEqual(audio_files(package), [])
            # the vehicle points at TPF3's own sound set; no sound script or audio is generated
            self.assertFalse((package / 'content/_assets/generated').exists())
            converted = parse((package / 'content/vehicle/train/unit.mdl').read_text('utf-8'))
            self.assertEqual(converted['metadata']['soundConfig']['soundSet']['name'],
                             '::/vehicle/train/shared/sound/train_electric_modern.snd')


if __name__ == '__main__':
    unittest.main()
