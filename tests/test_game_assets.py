import hashlib
from pathlib import Path
import tempfile
import unittest
import zipfile

from converter.adapters.game_assets import game_input, GameResources
from converter.parsers.lua import dumps
from converter.pipeline import convert, validate
from converter.transforms.sounds import convert_game_sound
from tests.test_conversion import fixture


class GameAssetTests(unittest.TestCase):
    def test_recursive_archive_mesh_and_original_override(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)/'mod'; root.mkdir()
            game=Path(td)/'game'; (game/'res').mkdir(parents=True)
            (root/'res/models/model').mkdir(parents=True)
            (root/'res/models/model/unit.mdl').write_text(dumps({'mesh':'vehicle/shared.msh','texture':'override.png'}),'utf-8')
            (root/'res/textures').mkdir(parents=True)
            (root/'res/textures/override.png').write_bytes(b'original override')
            with zipfile.ZipFile(game/'res/base.zip','w') as z:
                z.writestr('models/mesh/vehicle/shared.msh',dumps({'material':'shared.mtl'}))
                z.writestr('models/mesh/vehicle/shared.msh.blob',b'blob')
                z.writestr('models/material/shared.mtl',dumps({'image':'shared.png'}))
                z.writestr('textures/shared.png',b'texture')
                z.writestr('textures/override.png',b'wrong override')
                z.writestr('textures/unrelated.png',b'unused')
            before={p.relative_to(root):p.read_bytes() for p in root.rglob('*') if p.is_file()}
            with game_input(root,game) as (overlay,info):
                self.assertEqual(len(info['game_dependencies']),4)
                self.assertEqual((overlay/'res/textures/override.png').read_bytes(),b'original override')
                self.assertFalse((overlay/'res/textures/unrelated.png').exists())
                self.assertEqual((overlay/'res/models/mesh/vehicle/shared.msh.blob').read_bytes(),b'blob')
                self.assertEqual(info['game_dependencies']['res/textures/shared.png']['sha256'],hashlib.sha256(b'texture').hexdigest())
            self.assertEqual(before,{p.relative_to(root):p.read_bytes() for p in root.rglob('*') if p.is_file()})

    def test_vanilla_sound_audio_is_packaged_and_rewritten(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)/'mod'; model=fixture(root)
            model['metadata']['railVehicle']['soundSet']={'name':'stock_sound'}
            (root/'res/models/model/vehicle/train/unit.mdl').write_text(dumps(model),'utf-8')
            game=Path(td)/'game'; (game/'res/config/sound_set').mkdir(parents=True)
            (game/'res/audio/effects').mkdir(parents=True)
            (game/'res/config/sound_set/stock_sound.lua').write_text('local soundsetutil = require "soundsetutil"\nfunction data() local result = soundsetutil.makeSoundSet() soundsetutil.addEvent(result, "closeDoors", { "stock.wav" }, 1.0) return result end','utf-8')
            wav=b'RIFF'+(4).to_bytes(4,'little')+b'WAVE'
            (game/'res/audio/effects/stock.wav').write_bytes(wav)
            result=convert(root,Path(td)/'out',game_root=game)
            self.assertEqual(result['status'],'success',result['diagnostics'])
            package=Path(result['package_dir'])
            script=(package/'content/_assets/generated/sound/stock_sound.snd.lua').read_text('utf-8')
            self.assertIn('::/scripts/soundsetutil.lua',script)
            self.assertIn('/_assets/audio/effects/stock.wav',script)
            self.assertEqual((package/'content/_assets/audio/effects/stock.wav').read_bytes(),wav)
            self.assertIn('GAME_SOUNDSET_IMPORTED',{d['code'] for d in result['diagnostics']})
            self.assertTrue(validate(package)['valid'])

    def test_unknown_sound_api_is_diagnosed(self):
        with self.assertRaises(ValueError):
            convert_game_sound('function data() return soundsetutil.customSound() end','stock.lua',{}, {})

    def test_loose_resources_win_over_archive_and_res_selection(self):
        with tempfile.TemporaryDirectory() as td:
            res=Path(td)/'res'; (res/'textures').mkdir(parents=True)
            (res/'textures/image.png').write_bytes(b'loose')
            with zipfile.ZipFile(res/'base.zip','w') as z: z.writestr('textures/image.png',b'zip')
            resources=GameResources(res)
            self.assertEqual(resources.read(resources.find('IMAGE.PNG','mod.lua')),b'loose')

    def test_invalid_game_directory(self):
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(ValueError): GameResources(td)

    def test_archive_parent_traversal_is_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            res=Path(td)/'res'; res.mkdir()
            with zipfile.ZipFile(res/'bad.zip','w') as z: z.writestr('../escape.wav',b'data')
            with self.assertRaises(ValueError): GameResources(td)
