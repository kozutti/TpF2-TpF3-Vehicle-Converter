from __future__ import annotations
import json
import math
import re
from pathlib import Path
from converter.parsers.lua import parse, LuaParseError
from converter.codecs.mesh import validate_mesh
from converter.resolver import array, walk, resource_refs
from converter.adapters.tpf2_reader import diagnostic
from converter.transforms.icons import ICON_OUTPUT

def validate_package(package: Path):
    diagnostics = []
    package = Path(package)
    marker = package / '_metadata' / 'conversion_status.json'
    if marker.exists():
        try:
            saved = json.loads(marker.read_text(encoding='utf-8'))
            if saved.get('status') == 'partial':
                diagnostics.extend(saved.get('diagnostics', []))
                diagnostics.append(diagnostic('PARTIAL_PACKAGE', '必須処理が未完了の部分出力です。変換レポートを確認してください。'))
        except (ValueError, TypeError):
            diagnostics.append(diagnostic('CONVERSION_STATUS_INVALID', '変換状態ファイルが不正です。'))
    content = package / 'content'
    if not (package / 'mod.json').exists() or not content.is_dir():
        return {'status': 'invalid', 'valid': False, 'diagnostics': [diagnostic('PACKAGE_STRUCTURE_INVALID', 'mod.jsonとcontentが必要です。')]}
    try:
        mod = json.loads((package / 'mod.json').read_text(encoding='utf-8-sig'))
        if not re.fullmatch(r'[a-z0-9_.-]+', mod['modId']):
            diagnostics.append(diagnostic('MOD_ID_INVALID', 'modIdの文字列が不正です。'))
    except (ValueError, KeyError, TypeError) as exc:
        diagnostics.append(diagnostic('MOD_JSON_INVALID', str(exc), 'mod.json'))
    parsed = {}
    for path in sorted(content.rglob('*')):
        if not path.is_file():
            continue
        relative = path.relative_to(content).as_posix()
        if not re.fullmatch(r'[a-z0-9_./-]+', relative) and not ICON_OUTPUT.search(relative):
            diagnostics.append(diagnostic('RESOURCE_NAME_INVALID', 'リソースパスはASCII小文字が必要です。', relative))
        if re.search(r'\.(?:mdl|mtl|msh|ani|dds|tga|png|lua|blob|wav)_[0-9a-f]{8}$', relative):
            diagnostics.append(diagnostic('RESOURCE_EXTENSION_LOST', 'ファイル名の末尾が拡張子ではなくなっています。TPF3はこのファイルを読み込めません。', relative))
        if path.suffix not in {'.mdl', '.msh', '.mtl', '.ani', '.lua'}:
            continue
        if path.name.endswith(('.snd.lua','.script.lua')):
            from converter.transforms.sounds import validate_sound_script
            sound_diagnostics=validate_sound_script(content,path)
            if sound_diagnostics is not None:
                diagnostics.extend(sound_diagnostics)
                continue
        try:
            data = parse(path.read_text(encoding='utf-8-sig'), filename=relative)
            parsed[relative] = data
        except (LuaParseError, UnicodeError, ValueError) as exc:
            diagnostics.append(diagnostic('OUTPUT_PARSE_ERROR', str(exc), relative))
            continue
        for ref, at in resource_refs(data):
            if ref.startswith('::/'):
                continue
            local = content / ref.lstrip('/') if ref.startswith('/') else path.parent / ref
            if not local.resolve().is_relative_to(content.resolve()) or not local.is_file():
                diagnostics.append(diagnostic('OUTPUT_REFERENCE_MISSING', f'参照先がありません: {ref}', relative, location=at))
        for key, value, at in walk(data):
            if key == 'transf':
                values = array(value)
                if len(values) != 16 or any(not isinstance(x, (int, float)) or not math.isfinite(x) for x in values):
                    diagnostics.append(diagnostic('MATRIX_INVALID', '行列は有限数値16個が必要です。', relative, location=at))
        if path.suffix == '.msh':
            for entry in validate_mesh(data, Path(str(path) + '.blob')):
                diagnostics.append({**entry, 'file': relative})
        if path.suffix == '.mdl':
            sound=data.get('metadata',{}).get('soundConfig',{}).get('soundSet',{}).get('name','')
            if sound and not sound.startswith('::/'):
                sound_path=content/sound.lstrip('/') if sound.startswith('/') else path.parent/sound
                if not sound_path.resolve().is_relative_to(content.resolve()) or not Path(str(sound_path)+'.lua').is_file():
                    diagnostics.append(diagnostic('OUTPUT_SOUNDSET_MISSING',f'サウンドセットがありません: {sound}',relative))
            if data.get('version') != 2:
                diagnostics.append(diagnostic('MODEL_VERSION_INVALID', 'TPF3モデルversionは2です。', relative))
            last = 0
            for lod in array(data.get('lods', {})):
                start, end = lod.get('visibleFrom', 0), lod.get('visibleTo', 0)
                # 0..0 is accepted whether or not the LOD holds a mesh: group parents of multiple units use it with a
                # mesh and TPF3 loads them (checked in game 2026-10-12)
                empty_group = start == end == 0
                if not empty_group and (not isinstance(start, (int, float)) or not isinstance(end, (int, float)) or not 0 <= start < end or start < last):
                    diagnostics.append(diagnostic('LOD_RANGE_INVALID', 'LOD表示範囲が不正または重複しています。', relative))
                last = end
                names_for_lod = [v for key, v, at in walk(lod.get('node', {})) if key == 'name' and isinstance(v, str)]
                if len(names_for_lod) != len(set(names_for_lod)):
                    diagnostics.append(diagnostic('NODE_NAME_DUPLICATE', 'LOD内のノード名が重複しています。', relative))
            rail = data.get('metadata', {}).get('railVehicle', {})
            names = {v for key, v, at in walk(data.get('lods', {})) if key == 'name' and isinstance(v, str)}
            for axle in array(rail.get('config', {}).get('axles', {})):
                if axle not in names:
                    diagnostics.append(diagnostic('OUTPUT_AXLE_MISSING', f'車輪ノードがありません: {axle}', relative))
        if path.suffix == '.ani':
            times, transforms = array(data.get('times', {})), array(data.get('transfs', {}))
            if not times or len(times) != len(transforms):
                diagnostics.append(diagnostic('ANIMATION_LENGTH_INVALID', 'timesとtransfsの要素数が一致しません。', relative))
            elif any(not isinstance(t, (float, int)) or not math.isfinite(t) or t < 0 for t in times) or any(a >= b for a, b in zip(times, times[1:])):
                diagnostics.append(diagnostic('ANIMATION_TIME_INVALID', 'アニメーション時刻は有限値・非負・昇順が必要です。', relative))
            elif times[0] != 0:
                diagnostics.append(diagnostic('ANIMATION_START_NOT_ZERO', 'TPF3アニメーションの先頭時刻は0が必要です。', relative))
            for transform in transforms:
                values = array(transform)
                if len(values) != 16 or any(not isinstance(x, (int, float)) or not math.isfinite(x) for x in values):
                    diagnostics.append(diagnostic('ANIMATION_MATRIX_INVALID', 'アニメーション行列は有限数値16個が必要です。', relative))
        for key, animation, at in walk(data):
            if not isinstance(animation, dict) or animation.get('type') not in {'KEYFRAME', 'KEYFRAME_MATRIX'}:
                continue
            frames = array(animation.get('params', {}).get('keyframes', {}))
            times = [frame.get('time') if isinstance(frame, dict) else None for frame in frames]
            if not times or any(type(t) not in (int, float) or not math.isfinite(t) or t < 0 for t in times) or any(a >= b for a, b in zip(times, times[1:])):
                diagnostics.append(diagnostic('ANIMATION_TIME_INVALID', '埋込アニメーション時刻は有限値・非負・昇順が必要です。', relative, location=at))
            elif times[0] != 0:
                diagnostics.append(diagnostic('ANIMATION_START_NOT_ZERO', 'TPF3埋込アニメーションの先頭時刻は0が必要です。', relative, location=at))
    for relative, data in parsed.items():
        if not relative.endswith('.mdl'):
            continue
        for key, node, at in walk(data):
            if not isinstance(node, dict) or 'mesh' not in node:
                continue
            ref = node['mesh']
            target = ref.lstrip('/') if ref.startswith('/') else (Path(relative).parent / ref).as_posix()
            mesh = parsed.get(target)
            if mesh and len(array(node.get('materials', {}))) != len(array(mesh.get('subMeshes', {}))):
                diagnostics.append(diagnostic('MATERIAL_SLOT_MISMATCH', '材質数とサブメッシュ数が一致しません。', relative, location=at))
    from converter.transforms.texture_usage import validate_texture_uses
    diagnostics.extend(validate_texture_uses(content, parsed))
    from converter.transforms.lod import validate_lowest_lods
    diagnostics.extend(validate_lowest_lods(package, parsed))
    return {'status': 'valid' if not any(d['severity'] == 'error' for d in diagnostics) else 'invalid',
            'valid': not any(d['severity'] == 'error' for d in diagnostics), 'diagnostics': diagnostics,
            'resource_count': len(parsed), 'game_verified': False}
