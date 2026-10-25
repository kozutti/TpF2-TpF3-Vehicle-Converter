from __future__ import annotations
import hashlib
import os
from pathlib import Path
from converter.parsers.lua import parse, parse_mod_metadata, LuaParseError
from converter.parsers import lua_sandbox
from converter.adapters.source_repairs import repair_model_text, repair_model_references
from converter.resolver import array, placeholder_mesh, placeholder_texture, resolve, resource_refs, tpf3_character, walk
from converter.tpf3_sounds import default_horn, tpf3_shared_sound

TEXT_SUFFIXES = {'.mdl', '.msh', '.mtl', '.ani', '.lua'}
MAX_FILES = 25000
MAX_BYTES = 12 * 1024**3

def diagnostic(code, message, file='', severity='error', **extra):
    return {'severity': severity, 'code': code, 'message': message, 'file': file, **extra}

def cancelled(cancel):
    return cancel is not None and (cancel.is_set() if hasattr(cancel, 'is_set') else cancel())

def notify(progress, stage, current, total, message):
    if progress:
        progress({'stage': stage, 'current': current, 'total': total, 'message': message})

def sha256(path, cancel=None):
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b''):
            if cancelled(cancel):
                raise InterruptedError('変換を取り消しました。')
            digest.update(block)
    return digest.hexdigest()

def _module_source(root, files, name):
    """Lua source of a module the MOD ships (`require "a.b"` -> res/scripts/a/b.lua), or None."""
    if not isinstance(name, str) or '..' in name or name.startswith(('/', chr(92))) or ':' in name:
        return None
    wanted = ('res/scripts/' + name.replace('.', '/') + '.lua').casefold()
    for relative in files:
        if relative.casefold() == wanted:
            try:
                return (root / relative).read_text(encoding='utf-8-sig')
            except (OSError, UnicodeError):
                return None
    return None


def read_mod(root: Path, progress=None, cancel=None):
    diagnostics, files, parsed = [], {}, {}
    total_bytes = 0
    for directory, dirs, names in os.walk(root, followlinks=False):
        for child in list(dirs):
            path = Path(directory) / child
            if path.is_symlink() or path.is_junction():
                diagnostics.append(diagnostic('UNSAFE_LINK', 'リンクされたフォルダは読み込みません。', str(path)))
                dirs.remove(child)
        for name in sorted(names):
            if cancelled(cancel):
                raise InterruptedError('変換を取り消しました。')
            path = Path(directory) / name
            relative = path.relative_to(root).as_posix()
            if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
                diagnostics.append(diagnostic('UNSAFE_LINK', 'MOD外を参照するファイルです。', relative))
                continue
            size = path.stat().st_size
            total_bytes += size
            if len(files) >= MAX_FILES or total_bytes > MAX_BYTES:
                raise ValueError('入力は25,000ファイル、12 GiBまでです。')
            files[relative] = {'size': size, 'sha256': sha256(path, cancel)}
    # sound set scripts are code (soundsetutil calls); the sound conversion reads them separately
    selected = [p for p in files if Path(p).suffix.lower() in TEXT_SUFFIXES
                and not (p.startswith('res/config/sound_set/') and p.endswith('.lua'))]
    for i, relative in enumerate(selected):
        if cancelled(cancel):
            raise InterruptedError('変換を取り消しました。')
        notify(progress, 'analyze', i + 1, len(selected), relative)
        try:
            if files[relative]['size'] > 16 * 1024**2:
                raise ValueError('定義ファイルが16 MiBを超えています。')
            text = (root / relative).read_text(encoding='utf-8-sig')
            if len(text) > 16 * 1024**2:
                raise ValueError('定義ファイルが16 MiBを超えています。')
            if relative.endswith('.mdl'):
                text, repairs = repair_model_text(text, relative)
                diagnostics.extend(repairs)
            undefined = []
            if relative == 'mod.lua':
                parsed[relative] = parse_mod_metadata(text, filename=relative, undefined=undefined)
            else:
                parsed[relative] = parse(text, filename=relative, undefined=undefined)
            data = parsed[relative]
            if relative.endswith('.mdl'):
                diagnostics.extend(repair_model_references(data, relative, files))
            metadata = data.get('metadata') if isinstance(data, dict) else None
            rail = metadata.get('railVehicle') if isinstance(metadata, dict) else None
            if isinstance(rail, dict) and isinstance(rail.get('soundSet'), str):
                name = rail['soundSet'].strip()
                rail['soundSet'] = {'name': name} if name else {}
                diagnostics.append(diagnostic('LEGACY_SOUNDSET_NORMALIZED',
                    '旧形式のサウンド名をsoundSet.name形式に変換しました。', relative, severity='info'))
            for name, line in undefined[:20]:
                diagnostics.append(diagnostic('LUA_UNDEFINED_NAME', f'{line}行目の未定義の名前 {name!r} をnil（値なし）として扱いました。元MODの綴りミスの可能性があります。TPF2も同じ扱いで読み込みます。', relative, severity='warning'))
            for key, item, location in walk(parsed[relative]):
                if key == '__lua_function__':
                    diagnostics.append(diagnostic('MOD_SCRIPT_UNSUPPORTED', '独自Lua関数は実行せず、変換しません（スクリプト部分はTPF3向けの移植が必要です）。', relative, severity='warning', location=location))
        except (LuaParseError, ValueError, UnicodeError) as exc:
            if relative == 'strings.lua' and isinstance(exc, LuaParseError) and lua_sandbox.available():
                # translation tables that use helper functions (e.g. removeIndent) need real Lua; run them in the sandbox
                try:
                    parsed[relative] = lua_sandbox.evaluate(text, relative, lambda name: _module_source(root, files, name))
                    diagnostics.append(diagnostic('LUA_SANDBOX_EVALUATED',
                        '翻訳ファイルはLuaコードを使うため、隔離した環境（ファイル・通信・OSへのアクセスなし）で実行して読み込みました。',
                        relative, severity='info', parser_error=str(exc)))
                    continue
                except lua_sandbox.SandboxError as sandbox_error:
                    exc = f'{exc}（隔離実行も失敗: {sandbox_error}）'
            if Path(relative).suffix.lower() == '.lua' and not relative.startswith('res/config/multiple_unit/'):
                # mod.lua with requires, res/scripts/*.lua and other script files: code, not vehicle resources
                diagnostics.append(diagnostic('SCRIPT_FILE_NOT_READ', f'スクリプトファイルは変換の対象外です（{exc}）。', relative, severity='info'))
                continue
            if isinstance(exc, LuaParseError) and Path(relative).suffix.lower() in {'.mdl', '.msh', '.mtl', '.ani'}                     and lua_sandbox.available():
                # the MOD generates this resource with its own Lua code: evaluate it in the sandbox (no I/O, instruction limit)
                try:
                    parsed[relative] = lua_sandbox.evaluate(text, relative, lambda name: _module_source(root, files, name))
                    diagnostics.append(diagnostic('LUA_SANDBOX_EVALUATED',
                        'このファイルはLuaコードで生成されるため、隔離した環境（ファイル・通信・OSへのアクセスなし）で実行して読み込みました。',
                        relative, severity='info', parser_error=str(exc)))
                    continue
                except lua_sandbox.SandboxError as sandbox_error:
                    exc = f'{exc}（隔離実行も失敗: {sandbox_error}）'
            diagnostics.append(diagnostic('UNSUPPORTED_LUA_EXPRESSION', str(exc), relative))
    references = []
    for relative, data in parsed.items():
        for reference, location in resource_refs(data):
            target = resolve(reference, relative, files)
            references.append({'file': relative, 'reference': reference, 'location': location, 'resolved': target})
            if target is None and placeholder_texture(reference):
                continue  # TPF3 has the same placeholder texture; the writer points at it
            if target is None and placeholder_mesh(reference):
                continue  # TPF3 has the same placeholder mesh
            if target is None and tpf3_character(reference):
                continue  # TPF3 ships the same people / crew model; the writer points at it
            if target is None and relative.startswith('res/config/multiple_unit/'):
                continue  # the planner leaves out a consist whose cars do not exist
            if target is None and relative.endswith('.mdl') and location.startswith('/metadata/description/'):
                continue  # a store icon the MOD does not contain; the writer leaves the field out
            if target is None and relative.endswith('.mdl') and location == '/metadata/railVehicle/soundSet/horn':
                rail = data['metadata']['railVehicle']
                engines = array(rail.get('engines', {}))
                types = {engine.get('type') for engine in engines if isinstance(engine, dict)}
                replacement = default_horn(types)
                rail['soundSet']['horn'] = replacement
                references[-1]['resolved'] = replacement
                diagnostics.append(diagnostic('HORN_AUDIO_FALLBACK',
                    f'警笛音声 {reference} が元MOD・依存MOD・TPF2本体に見つかりません。TPF3標準の警笛 {replacement} で代用します（原音とは異なります）。',
                    relative, severity='warning', location=location, original=reference, replacement=replacement))
                continue
            if target is None and isinstance(reference, str) and reference.lower().endswith('.wav') and not tpf3_shared_sound(reference):
                diagnostics.append(diagnostic('SOUND_FILE_MISSING', f'音声ファイルが見つかりません: {reference}。この音は出力しません。',
                                              relative, severity='warning', location=location))
                continue
            if target is None and isinstance(reference, str) and reference.lower().endswith('.wav') and tpf3_shared_sound(reference):
                continue  # a TPF2 stock sound; TPF3 ships the same file and the writer points at it
            if target is None and isinstance(reference, str) and (reference.lower().endswith(('.msh', '.ani'))
                                                                  or (reference.lower().endswith('.mdl') and '/cargoSlotProvider/' in location)):
                # TPF2 skips a part whose mesh/animation is missing; the writer leaves that part out
                diagnostics.append(diagnostic('GEOMETRY_MISSING', f'ファイルが見つかりません: {reference}。その部品を省きます。',
                                              relative, severity='warning', location=location))
                continue
            if target is None and isinstance(reference, str) and reference.lower().endswith('.mtl'):
                # TPF2 still loads the mesh; TPF3 would remove the whole model, so the writer substitutes its placeholder material
                diagnostics.append(diagnostic('MATERIAL_MISSING', f'材質が見つかりません: {reference}。TPF3の仮の材質で代用します。',
                                              relative, severity='warning', location=location))
                continue
            if target is None and relative.endswith('.mdl') and location.endswith('/groupFileName'):
                continue  # the writer reports a group model that does not exist and leaves the vehicle ungrouped
            if target is None and relative.endswith('.mtl') and isinstance(reference, str) \
                    and reference.lower().endswith(('.dds', '.tga', '.png')):
                # A texture neither the MOD nor the MODs next to it contain. TPF2 still loads the material; TPF3
                # would drop the whole vehicle, so the writer substitutes a placeholder texture.
                diagnostics.append(diagnostic('TEXTURE_MISSING', f'テクスチャが見つかりません: {reference}。仮のテクスチャで代用します。',
                                              relative, severity='warning', location=location))
                continue
            if target is None:
                diagnostics.append(diagnostic('UNRESOLVED_REFERENCE', f'参照先がありません: {reference}', relative, location=location))
    models = [p for p, d in parsed.items() if p.endswith('.mdl') and isinstance(d, dict) and 'railVehicle' in d.get('metadata', {})]
    rail = set(models)
    roots = {p for p in parsed if (p.endswith('.mdl') and (p in rail or not isinstance(parsed[p], dict) or 'metadata' not in parsed[p]))
             or p.startswith('res/config/multiple_unit/')}
    reachable, pending = set(roots), list(roots)
    edges = {}
    for reference in references:
        if reference['resolved'] in files:
            edges.setdefault(reference['file'], []).append(reference['resolved'])
    while pending:
        for target in edges.get(pending.pop(), []):
            if target not in reachable:
                reachable.add(target)
                pending.append(target)
    for entry in diagnostics:
        if entry['code'] == 'UNRESOLVED_REFERENCE' and entry['file'] not in reachable:
            entry['severity'] = 'warning'
            entry['message'] = '未使用資産の' + entry['message']
    missing = [d for d in diagnostics if d['code'] in {'UNRESOLVED_REFERENCE', 'TEXTURE_MISSING', 'GEOMETRY_MISSING', 'MATERIAL_MISSING'} and d['severity'] != 'info'
               and not d['message'].startswith('未使用資産の') and d['file'] in reachable]
    if missing:
        diagnostics.append(diagnostic('DEPENDENCY_MOD_SUSPECTED',
            f'使われている資産のうち {len(missing)} 件が、元MOD・同じフォルダーの他MOD・TPF2本体のどこにも見つかりません。'
            '元MODの入れ忘れ・綴りやパスの違い（TPF2でも同じ表示になっている可能性が高い）か、まだ入手していない依存MODの可能性があります。'
            '依存MODがある場合は、同じフォルダー（Steam Workshopのcontent/1066780など）に置いて再変換してください。'
            '見つからないテクスチャ・材質はTPF3の仮のもので代用し、メッシュ・アニメーション・コンテナのモデルは該当部品を省きます。', severity='warning'))
    if not models:
        diagnostics.append(diagnostic('NO_SUPPORTED_VEHICLES', '鉄道車両モデルが見つかりません。'))
    return {'schema_version': 1, 'source': str(root), 'files': files, 'parsed': parsed,
            'references': references, 'reachable': sorted(reachable), 'vehicles': models, 'vehicle_count': len(models),
            'diagnostics': diagnostics}
