"""Check the distributed ZIP from a separate Unicode/space path without Python in PATH."""
from pathlib import Path
from copy import deepcopy
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import uuid
import zipfile

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root))
sys.path.insert(0, str(root / 'tests'))
from test_conversion import fixture
from test_texture_usage import dxt1
from converter.parsers.lua import parse, dumps
from converter.resolver import array
from converter.transforms.textures import inspect_dds
from converter.transforms.lod import validate_lowest_lods

test_root = root / '.build' / ('別PC 起動確認_' + uuid.uuid4().hex[:8])
test_root.mkdir(parents=True)
release_zip = Path(sys.argv[1]) if len(sys.argv) > 1 else root / 'release' / 'TPF3VehicleConverter-Windows-x64.zip'
with zipfile.ZipFile(release_zip) as archive:
    archive.extractall(test_root)
portable = test_root / 'TPF3VehicleConverter'
source = test_root / '入力 MOD'
fixture(source)
game = test_root / '模擬 TPF2'
(game/'res/config/sound_set').mkdir(parents=True)
(game/'res/audio/effects').mkdir(parents=True)
(game/'res/config/sound_set/stock_sound.lua').write_text('local soundsetutil = require "soundsetutil"\nfunction data() local result = soundsetutil.makeSoundSet() soundsetutil.addEvent(result, "closeDoors", { "stock.wav" }, 1.0) return result end','utf-8')
(game/'res/audio/effects/stock.wav').write_bytes(b'RIFF'+(4).to_bytes(4,'little')+b'WAVE')
model_path=source/'res/models/model/vehicle/train/unit.mdl'
model=parse(model_path.read_text('utf-8'))
model['metadata']['railVehicle']['soundSet']={'name':'stock_sound'}
model_path.write_text(dumps(model),'utf-8')
texture = source / 'res/textures/unit/normal.dds'
texture.parent.mkdir(parents=True)
texture.write_bytes(dxt1())
material_file = source / 'res/models/material/vehicle/train/unit/plain.mtl'
material = parse(material_file.read_text('utf-8'))
material['params']['map_normal'] = {'fileName': 'unit/normal.dds'}
material_file.write_text(dumps(material), encoding='utf-8')
# Independent DDS inputs force the frozen executable to spawn its worker pool.
for index in range(1, 64):
    (texture.parent / f'normal{index}.dds').write_bytes(dxt1())
    extra_material = deepcopy(material)
    extra_material['params']['map_normal']['fileName'] = f'unit/normal{index}.dds'
    (material_file.parent / f'plain{index}.mtl').write_text(dumps(extra_material), 'utf-8')
    # The planner intentionally leaves out unused materials. Reference each one
    # from a mesh node so these textures enter the real conversion pipeline.
    model['lods'][1]['node']['children'][3 + index] = {
        'name': f'texture_test_{index}', 'mesh': 'vehicle/train/unit/wheel.msh',
        'materials': {1: f'vehicle/train/unit/plain{index}.mtl'},
        'transf': model['lods'][1]['node']['transf']}
model_path.write_text(dumps(model), 'utf-8')
animation_file = source / 'res/models/animation/vehicle/train/unit/open.ani'
animation = parse(animation_file.read_text('utf-8'))
animation['times'][1] = 100
animation_file.write_text(dumps(animation), encoding='utf-8')
env = os.environ.copy()
for name in ['PYTHONHOME', 'PYTHONPATH', 'TCL_LIBRARY', 'TK_LIBRARY', 'VIRTUAL_ENV']:
    env.pop(name, None)
env['PATH'] = str(Path(os.environ['SystemRoot']) / 'System32')
cli = portable / 'TPF3VehicleConverterCLI.exe'
def execute(exe, *args):
    result = subprocess.run([str(exe), *map(str, args)], cwd=portable, env=env,
                            capture_output=True, text=True, encoding='utf-8', timeout=120)
    if result.returncode:
        raise RuntimeError(f'{exe.name}: {result.returncode}\n{result.stdout}\n{result.stderr}')
    return result.stdout
version = execute(cli, '--version').strip()
invalid_workers = subprocess.run([str(cli), 'convert', str(source), '--texture-workers', '65'],
                                 cwd=portable, env=env, capture_output=True, text=True, encoding='utf-8', timeout=30)
assert invalid_workers.returncode == 2 and 'invalid choice' in invalid_workers.stderr, invalid_workers
reference=root/'tpf2参考/9000000009/res/models/mesh/vehicle/train/JNR_Tc111_1/Body_lod0.msh'
wasm_checked=False
if reference.exists():
    large_source=test_root/'汎用 LOD'
    large_model=fixture(large_source)
    body=large_source/'res/models/mesh/vehicle/train/unit/body.msh'
    body.write_bytes(reference.read_bytes())
    original_blob=Path(str(reference)+'.blob').read_bytes()
    Path(str(body)+'.blob').write_bytes(original_blob)
    body2=large_source/'res/models/mesh/vehicle/train/unit/body2.msh'  # a second distinct large mesh: two simplification jobs for the process pool
    body2.write_bytes(reference.read_bytes())
    Path(str(body2)+'.blob').write_bytes(original_blob)
    definition=parse(reference.read_text('utf-8-sig'))
    root_node=large_model['lods'][1]['node']
    root_node['children'][4]={'name':'body','mesh':'vehicle/train/unit/body.msh','materials':{i:'vehicle/train/unit/plain.mtl' for i in definition['subMeshes']},'transf':root_node['transf']}
    root_node['children'][5]=dict(root_node['children'][4],name='body2',mesh='vehicle/train/unit/body2.msh')
    large_model['lods'][1]['visibleTo']=500
    large_model['lods'][2]=deepcopy(large_model['lods'][1])
    large_model['lods'][2].update(visibleFrom=500,visibleTo=2000)
    (large_source/'res/models/model/vehicle/train/unit.mdl').write_text(dumps(large_model),'utf-8')
    large_run=subprocess.run([str(cli),'convert',str(large_source),'--output',str(test_root/'LOD出力'),'--texture-workers','4'],cwd=portable,
                             env=dict(env,TPF3_LOD_TRACE='1'),capture_output=True,text=True,encoding='utf-8',timeout=600)
    assert large_run.returncode==0,large_run.stderr
    large_result=json.loads(large_run.stdout)
    assert large_result['status']=='success',large_result
    assert re.search(r'\[lod\] 2 distinct meshes to simplify, workers 2,',large_run.stderr),large_run.stderr
    assert len(re.findall(r'\[lod\] job ',large_run.stderr))==2,large_run.stderr  # job lines are printed by pool workers only: the frozen executable used the process pool
    large_content=Path(large_result['package_dir'])/'content'
    assert (large_content/'_assets/mesh/vehicle/train/unit/body.msh.blob').read_bytes()==original_blob
    assert not any(d['severity']=='error' for d in validate_lowest_lods(large_content))
    wasm_checked=True
converted = json.loads(execute(cli, 'convert', source, '--output', test_root / '出力', '--game-root',game,
                               '--texture-workers', 64))
assert converted['status'] == 'success', converted
validated = json.loads(execute(cli, 'validate', converted['package_dir']))
assert validated['valid'], validated
content = Path(converted['package_dir']) / 'content'
assert (content/'_assets/audio/effects/stock.wav').read_bytes() == (game/'res/audio/effects/stock.wav').read_bytes()
assert 'GAME_SOUNDSET_IMPORTED' in {d['code'] for d in converted['diagnostics']}
normal_files = list((content / '_assets/converted_textures').glob('*_normal.dds'))
assert len(normal_files) == 64 and all(inspect_dds(p)['format'] == 'BC5' for p in normal_files)
converted_animations = list(content.rglob('*.ani'))
assert converted_animations and array(parse(converted_animations[0].read_text('utf-8'))['times']) == [0, 100, 3100]
zip_source = test_root / '入力 MOD.zip'
with zipfile.ZipFile(zip_source, 'w', zipfile.ZIP_DEFLATED) as archive:
    for path in source.rglob('*'):
        if path.is_file():
            archive.write(path, 'mod/' + path.relative_to(source).as_posix())
zip_result = json.loads(execute(cli, 'convert', zip_source, '--output', test_root / 'ZIP出力','--game-root',game))
assert zip_result['status'] == 'success', zip_result
# folder-wide conversion: an add-on that borrows its base MOD's meshes, and a script-only MOD that must be skipped
library = test_root / '一括 MOD'
addon, base, script_mod = library / '200_addon', library / '100_base', library / '300_script'
fixture(addon)
borrowed = base / 'res/models/mesh/vehicle/train/unit'
borrowed.mkdir(parents=True)
for mesh_file in sorted((addon / 'res/models/mesh/vehicle/train/unit').iterdir()):
    shutil.move(str(mesh_file), str(borrowed / mesh_file.name))
(base / 'mod.lua').write_text('function data() return {info={name="Base pack"}} end', 'utf-8')
(script_mod / 'res/scripts').mkdir(parents=True)
(script_mod / 'res/scripts/tool.lua').write_text('x', 'utf-8')
(script_mod / 'mod.lua').write_text('function data() return {info={name="Script"}} end', 'utf-8')
batch_result = json.loads(execute(cli, 'batch', library, '--output', test_root / '一括出力', '--singles', 'group'))
batch_rows = {row['folder']: row for row in batch_result['batch']['mods']}
assert batch_rows['200_addon']['status'] == 'success' and batch_rows['200_addon']['reference_mods'] == {'100_base': 2}, batch_rows
assert batch_rows['300_script']['status'] == 'skipped' and 'スクリプト' in batch_rows['300_script']['reason'], batch_rows
assert (Path(batch_result['run_dir']) / 'batch_summary.md').is_file()
# MODs converted at the same time, each in its own process (spawned from the frozen EXE; the add-on borrows from its base)
parallel_result = json.loads(execute(cli, 'batch', library, '--output', test_root / '一括並列出力', '--singles', 'group',
                                     '--mod-workers', 2, '--texture-workers', 2))
parallel_rows = {row['folder']: row for row in parallel_result['batch']['mods']}
assert [r['folder'] for r in parallel_result['batch']['mods']] == [r['folder'] for r in batch_result['batch']['mods']], parallel_rows
assert {k: r['status'] for k, r in parallel_rows.items()} == {k: r['status'] for k, r in batch_rows.items()}, parallel_rows
assert parallel_rows['200_addon']['reference_mods'] == {'100_base': 2}, parallel_rows
assert parallel_result['batch']['mod_workers'] == 2, parallel_result['batch']
gui_report = test_root / 'gui-smoke.json'
execute(portable / 'TPF3VehicleConverter.exe', 'gui', '--smoke-test', '--smoke-report', gui_report)
gui = json.loads(gui_report.read_text('utf-8'))
assert gui['status'] == 'smoke_passed' and gui['dnd'], gui
# Exercise multiprocessing in the windowed EXE, where stdout/stderr are None.
gui_conversion_report = test_root / 'gui-conversion.json'
execute(portable / 'TPF3VehicleConverter.exe', 'convert', source, '--output', test_root / 'GUI変換出力',
        '--game-root', game, '--texture-workers', 64, '--json', gui_conversion_report)
gui_conversion = json.loads(gui_conversion_report.read_text('utf-8'))
assert gui_conversion['status'] == 'success', gui_conversion
gui_content = Path(gui_conversion['package_dir']) / 'content'
assert {p.name: p.read_bytes() for p in (gui_content / '_assets/converted_textures').glob('*.dds')} == {
    p.name: p.read_bytes() for p in (content / '_assets/converted_textures').glob('*.dds')}
report = {'version': version, 'extracted_path': str(portable), 'python_in_path': False,
          'frozen_conversion': converted['status'], 'frozen_zip_conversion': zip_result['status'],
          'frozen_validation': validated['valid'], 'frozen_bc5_conversion': True,
          'frozen_animation_zero_start': True, 'gui': gui}
report['frozen_game_asset_import']=True
report['frozen_batch_and_dependency_import']=True
report['frozen_large_lod_conversion']=wasm_checked
report['frozen_parallel_dds_workers']=64
report['frozen_windowed_parallel_dds_workers']=64
report['frozen_texture_worker_upper_limit']=64
report['frozen_parallel_mods']=2
(root / '.build' / 'portable-verification.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps(report, ensure_ascii=True, indent=2))
