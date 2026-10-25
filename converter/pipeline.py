"""Shared analyze / plan / convert / validate entry points for CLI and D&D."""
from __future__ import annotations
from contextlib import contextmanager
from datetime import datetime
import json
import shutil
import stat
import tempfile
import uuid
import zipfile
from pathlib import Path, PurePosixPath
import re
from converter.adapters.tpf2_reader import read_mod, sha256, diagnostic, cancelled, notify
from converter.adapters.tpf3_writer import write_definition
from converter.planner import build_plan, PROFILE_ID
from converter.parsers.lua import dumps
from converter.reporting import write_json, write_reports
from converter.validators import validate_package
from converter import __version__
from converter.adapters.game_assets import game_input, SiblingIndex
from converter.transforms.texture_usage import DEFAULT_TEXTURE_WORKERS, validate_texture_workers

@contextmanager
def input_root(source, progress=None, cancel=None):
    source = Path(source).resolve()
    temporary = None
    try:
        if source.is_file() and source.suffix.lower() == '.zip':
            temporary = tempfile.TemporaryDirectory(prefix='tpf_converter_')
            root = Path(temporary.name)
            with zipfile.ZipFile(source) as archive:
                members = archive.infolist()
                if len(members) > 25000 or sum(m.file_size for m in members) > 12 * 1024**3:
                    raise ValueError('ZIPは25,000ファイル・展開12 GiBまでです。')
                seen = set()
                for member in members:
                    name = member.filename.replace('\\', '/')
                    parts = PurePosixPath(name).parts
                    from converter.resolver import RESERVED
                    raw_parts = name.rstrip('/').split('/')
                    if not parts or name.startswith('/') or any(p in {'', '.', '..'} for p in raw_parts) or re.search(r'[<>:"|?*\x00-\x1f]', name) or any(p.endswith(('.', ' ')) or p.casefold().split('.')[0] in RESERVED for p in parts):
                        raise ValueError(f'ZIPに不正なパスがあります: {name}')
                    if stat.S_ISLNK(member.external_attr >> 16):
                        raise ValueError('ZIP内のシンボリックリンクは受け入れません。')
                    canonical = name.rstrip('/').casefold()
                    if canonical in seen:
                        raise ValueError(f'ZIP内のパスが重複しています: {name}')
                    seen.add(canonical)
                    if member.file_size > 1024**3 and member.file_size / max(member.compress_size, 1) > 500:
                        raise ValueError('ZIP圧縮率が安全上限を超えています。')
                for i, member in enumerate(members):
                    if cancelled(cancel):
                        raise InterruptedError('変換を取り消しました。')
                    target = root.joinpath(*PurePosixPath(member.filename.replace('\\', '/')).parts)
                    if member.is_dir():
                        target.mkdir(parents=True, exist_ok=True)
                        continue
                    target.parent.mkdir(parents=True, exist_ok=True)
                    notify(progress, 'extract', i + 1, len(members), member.filename)
                    with archive.open(member) as src, target.open('wb') as dest:
                        while True:
                            if cancelled(cancel):
                                raise InterruptedError('変換を取り消しました。')
                            block = src.read(1024 * 1024)
                            if not block:
                                break
                            dest.write(block)
        elif source.is_dir():
            root = source
        else:
            raise ValueError('TPF2 MODフォルダまたはZIPを指定してください。')
        if not (root / 'mod.lua').is_file():
            matches = list(root.glob('*/mod.lua'))
            if len(matches) != 1:
                raise ValueError('mod.luaを持つMODフォルダが1個必要です。')
            root = matches[0].parent
        yield root
    finally:
        if temporary:
            temporary.cleanup()


def resolve_siblings(source, siblings='auto'):
    """Where to look for assets that this MOD borrows from other MODs.

    'auto' uses the folder that holds the MOD (for example the Steam Workshop
    folder) when other MODs sit next to it; a SiblingIndex is used as given;
    None/False turns the search off. The search is lazy: nothing is read
    unless a referenced file is missing from the MOD itself.
    """
    if siblings in (None, False):
        return None
    if isinstance(siblings, SiblingIndex):
        return siblings
    source = Path(source).resolve()
    if source.is_dir():
        folder = source.parent
        for parent in source.parents:
            if parent.name == '1066780' and parent.parent.name == 'content' and parent.parent.parent.name == 'workshop':
                folder = parent
                break
        index = SiblingIndex(folder)
        if index.has_other_mods(exclude=source):
            return index
    return None


_INFO_KEYS = ('modname', 'moddesc', 'desc', 'name_', 'description')


def strip_author_prefix(name, stem):
    """`Ab1_Car_2` for `Car_2_RExp.mdl` -> `Car_2_RExp`: the key is the file name behind one or more leading
    tokens (an author tag such as `Ab1_`, or `name_model_`). A key that is itself the intended name (`Unit180`) or whose file
    name would say less (`n700_01` -> `01`) is not touched."""
    if sum(c.isalpha() for c in stem) < 2:
        return None
    tokens = name.split('_')
    for i in range(1, len(tokens)):
        rest = '_'.join(tokens[i:]).lower()
        if rest and stem.lower().startswith(rest):
            return stem
    return None


def formation_name(relative, parsed, files, translations):
    """`<formation name> <position>両目` for a model that a multiple-unit config lists (the config's `name` is translated like
    any other name; a model listed at several positions gets all of them). None when no config lists the model."""
    from converter.adapters.tpf3_writer import is_multiple_unit
    from converter.resolver import array, resolve
    tables = [t for t in (translations.get('ja'), translations.get('en')) if isinstance(t, dict)]
    for path in sorted(p for p in parsed if is_multiple_unit(p)):
        config = parsed[path]
        if not isinstance(config, dict):
            continue
        entries = [e for e in array(config.get('vehicles', {})) if isinstance(e, dict)]
        positions = [i for i, e in enumerate(entries, 1) if isinstance(e.get('name'), str) and resolve(e['name'], path, files) == relative]
        label = config.get('name')
        if not positions or not isinstance(label, str) or not label:
            continue
        for table in tables:
            if isinstance(table.get(label), str) and table[label]:
                label = table[label]
                break
        else:
            if re.fullmatch(r'[A-Za-z0-9_\-]+', label):
                continue  # an untranslated key says nothing
        return f"{label} {'・'.join(str(i) for i in positions)}両目"
    return None


def guess_unresolved_vehicle_name(model, relative, translations, diagnostics, parsed=None, files=None):
    """A model whose name is a translation key that the MOD never defines (an author's copy-paste slip) shows the raw key.
    Use the translation whose key is the longest prefix of the model's file name (`unit285_x.mdl` -> `unit285`)."""
    description = model.get('metadata', {}).get('description') if isinstance(model.get('metadata'), dict) else None
    name = description.get('name') if isinstance(description, dict) else None
    if isinstance(name, str) and not name.strip():
        # `name = _("")`: the author left the name out, and a row without a name in the depot list says nothing.
        stem = Path(relative).stem
        if stem:
            description['name'] = stem
            diagnostics.append(diagnostic('NAME_EMPTY_FILE_NAME',
                f'車両名が空です。モデルのファイル名 {stem!r} を表示名にしました。', relative, severity='info'))
        return
    if not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9_\-]+', name):
        return
    tables = [t for t in (translations.get('ja'), translations.get('en')) if isinstance(t, dict)]
    if not tables or any(name in t for t in tables):
        return
    stem = Path(relative).stem.lower()
    for table in tables:
        keys = [k for k, v in table.items() if isinstance(k, str) and isinstance(v, str) and v and not k.startswith(_INFO_KEYS)
                and (stem == k.lower() or stem.startswith(k.lower()))]
        if keys:
            key = max(keys, key=len)
            description['name'] = table[key]
            diagnostics.append(diagnostic('NAME_KEY_UNDEFINED_GUESSED',
                f'車両名の翻訳キー {name!r} が元MODに定義されていません。ファイル名に一致する {key!r} の訳「{table[key]}」を使いました。', relative, severity='info'))
            return
    stripped = strip_author_prefix(name, Path(relative).stem)
    if stripped:
        description['name'] = stripped
        diagnostics.append(diagnostic('NAME_KEY_UNDEFINED_FILE_NAME',
            f'車両名の翻訳キー {name!r} が元MODに定義されていません。作者名などの接頭辞を外し、モデルのファイル名 {stripped!r} を表示名にしました。', relative, severity='info'))
        return
    named = formation_name(relative, parsed, files, translations) if parsed is not None else None
    if named:
        description['name'] = named
        diagnostics.append(diagnostic('NAME_FROM_FORMATION',
            f'車両名の翻訳キー {name!r} が元MODに定義されていません。編成の設定（multiple_unit）の名前と、編成の中の位置から、表示名を「{named}」にしました。', relative, severity='info'))
        return
    diagnostics.append(diagnostic('NAME_KEY_UNDEFINED',
        f'車両名の翻訳キー {name!r} が元MODに定義されていません。そのまま表示されます（TPF2でも同じ可能性があります）。', relative, severity='warning'))


def auto_game_root(source, game_root):
    """The TPF2 install next to a Steam Workshop MOD (<library>/steamapps/common/Transport Fever 2), when none was given.

    Base-game assets that a MOD borrows (animals, stock sounds, textures ...) are then taken from it, read-only, exactly as
    if the user had picked the folder; a MOD outside a Steam library keeps working without it.
    """
    if game_root:
        return game_root
    try:
        parts = Path(source).resolve().parts
    except OSError:
        return None
    lowered = [part.lower() for part in parts]
    if 'steamapps' in lowered:
        candidate = Path(*parts[:lowered.index('steamapps') + 1]) / 'common' / 'Transport Fever 2'
        if (candidate / 'res').is_dir():
            return str(candidate)
    return None

def analyze(source: Path, progress=None, cancel=None, game_root=None, siblings='auto'):
    game_root = auto_game_root(source, game_root)
    with input_root(source, progress, cancel) as original, game_input(original,game_root,progress,cancel,
            siblings=resolve_siblings(source, siblings),exclude=original) as (root,game_info):
        result = read_mod(root, progress, cancel)
        result.update(game_info)
        result['source'] = str(Path(source).resolve())
        result['status'] = 'analyzed'
        return result

def plan(source: Path, progress=None, cancel=None, game_root=None, siblings='auto'):
    return build_plan(analyze(source, progress, cancel, game_root, siblings))

def batch(source: Path, output: Path, progress=None, cancel=None, game_root=None, single_vehicles='hide',
          search_siblings=True, only=None, texture_workers=DEFAULT_TEXTURE_WORKERS, mod_workers=1):
    """Convert every MOD in a folder; see converter.batch."""
    from converter.batch import batch as run_batch
    return run_batch(source, output, progress, cancel, game_root, single_vehicles, search_siblings, only, texture_workers, mod_workers)

def validate(package: Path):
    return validate_package(Path(package))

def convert(source: Path, output: Path, progress=None, cancel=None, game_root=None, single_vehicles='hide', siblings='auto',
            texture_workers=DEFAULT_TEXTURE_WORKERS, mods_dir=None, mods_name=None):
    """Convert one MOD. With `mods_dir`, a successful package is moved out of the run folder into
    `mods_dir/<mods_name>` (default `<id>_<vehicle name>`), ready to copy into the game's `mods` folder."""
    validate_texture_workers(texture_workers)
    from converter.transforms.singles import MODES
    if single_vehicles not in MODES:
        raise ValueError(f'単体車両の扱いは {", ".join(MODES)} のいずれかです。')
    source, output = Path(source).resolve(), Path(output).resolve()
    game_root = auto_game_root(source, game_root)
    if source == output or (source.is_dir() and output.is_relative_to(source)):
        raise ValueError('元MOD内には出力できません。別のフォルダを指定してください。')
    run_id = uuid.uuid4().hex[:8]
    run = output / (datetime.now().strftime('run_%Y%m%d_%H%M%S_') + run_id)
    run.mkdir(parents=True, exist_ok=False)
    analysis = {'source': str(source), 'files': {}, 'vehicle_count': 0}
    conversion_plan = {}
    result = {'run_id': run_id, 'run_dir': str(run), 'package_dir': None, 'status': 'failed', 'single_vehicles': single_vehicles,
              'diagnostics': [], 'game_verified': False}
    staging = run / '_work'
    relocated = None  # (new folder, key prefix the manifest keeps for it)
    try:
        with input_root(source, progress, cancel) as original, game_input(original,game_root,progress,cancel,
                siblings=resolve_siblings(source, siblings),exclude=original) as (root,game_info):
            analysis = read_mod(root, progress, cancel)
            analysis.update(game_info)
            analysis['source'] = str(source)
            conversion_plan = build_plan(analysis)
            diagnostics = list(conversion_plan['diagnostics'])
            result['diagnostics'] = diagnostics
            imported_from = analysis.get('imported_from') or {}
            result['reference_mods'] = imported_from
            if imported_from:
                diagnostics.append(diagnostic('DEPENDENCY_ASSETS_IMPORTED',
                    '参照先が元MODに無かった資産を取り込みました: ' + '、'.join(f'{name}（{count}件）' for name, count in imported_from.items())
                    + '。他の作者の資産を含むため、変換結果は個人利用にとどめ、再配布しないでください。', severity='info'))
            for sound_set, addon in (analysis.get('sound_overrides') or {}).items():
                diagnostics.append(diagnostic('SOUND_SET_OVERRIDDEN_BY_ADDON',
                    f'サウンド定義を、サウンド用の追加MOD「{addon}」のものに差し替えました（TPF2で両方を有効にしたときと同じ扱い）。', sound_set, severity='info'))
            for sound_set, addons in analysis.get('sound_override_conflicts') or []:
                diagnostics.append(diagnostic('SOUND_SET_OVERRIDE_AMBIGUOUS',
                    f'サウンド定義を差し替える追加MODが複数あるため、差し替えていません: {"、".join(addons)}。', sound_set, severity='warning'))
            if any(d['code'] == 'OUTPUT_PATH_COLLISION' for d in diagnostics):
                raise ValueError('出力パスの衝突があるため書き出しを中止しました。')
            package = staging / conversion_plan['mod_id']
            content = package / 'content'
            content.mkdir(parents=True)
            mod_info = analysis['parsed'].get('mod.lua', {}).get('info', {})
            translations = analysis['parsed'].get('strings.lua', {})
            def translated(value):
                if isinstance(value, str):
                    return translations.get('ja', {}).get(value, translations.get('en', {}).get(value, value))
                return value
            write_json(package / 'mod.json', {'modId': conversion_plan['mod_id'], 'revision': 1,
                'dependencies': None, 'incompatibilities': None, 'options': None, 'params': None,
                'preRunScript': {'fileName': ''}, 'runScript': {'fileName': ''}, 'postRunScript': {'fileName': ''},
                'severityAdd': 'None', 'severityRemove': 'Warning'})
            from converter.resolver import array
            write_json(package / '_metadata' / 'modinfo.json', {'name': translated(mod_info.get('name', source.stem)),
                'summary': translated(mod_info.get('name', source.stem)), 'description': translated(mod_info.get('description', '')),
                'authors': array(mod_info.get('authors', {})), 'tags': array(mod_info.get('tags', {})), 'url': '',
                'localization': {language: {'name': values.get(mod_info.get('name'), mod_info.get('name', source.stem)),
                               'description': values.get(mod_info.get('description'), '')} for language, values in translations.items() if isinstance(values, dict)}})
            for index, (relative, destination) in enumerate(conversion_plan['mapping'].items()):
                if cancelled(cancel):
                    raise InterruptedError('変換を取り消しました。')
                notify(progress, 'convert', index + 1, len(conversion_plan['mapping']), relative)
                target = content / destination
                target.parent.mkdir(parents=True, exist_ok=True)
                if relative in analysis['parsed'] and relative.endswith(('.mdl', '.mtl', '.msh', '.ani', '.lua')):
                    original = analysis['parsed'][relative]
                    value = write_definition(original, relative, analysis['files'], conversion_plan['mapping'], conversion_plan['mod_id'], [], analysis['parsed'],
                                             analysis.get('game_soundsets'))
                    def translate_fields(data):
                        if isinstance(data, dict):
                            return {k: translated(v) if k in {'name', 'description', 'desc'} and isinstance(v, str) else translate_fields(v) for k, v in data.items()}
                        return data
                    value = translate_fields(value)
                    if relative.endswith('.mdl'):
                        guess_unresolved_vehicle_name(value, relative, translations, diagnostics, analysis['parsed'], analysis['files'])
                    target.write_text(dumps(value), encoding='utf-8')
                else:
                    with (root / relative).open('rb') as src, target.open('wb') as dest:
                        while True:
                            if cancelled(cancel):
                                raise InterruptedError('変換を取り消しました。')
                            block = src.read(1024 * 1024)
                            if not block:
                                break
                            dest.write(block)
                if sha256(root / relative, cancel) != analysis['files'][relative]['sha256']:
                    raise ValueError(f'処理中に入力が変更されました: {relative}')
            from converter.transforms.texture_usage import convert_package_textures
            from converter.transforms.lod import optimize_lowest_lods
            from converter.transforms.sounds import write_soundsets
            from converter.transforms.icons import make_store_icons
            from converter.transforms.mod_preview import write_mod_previews
            diagnostics.extend(write_soundsets(package,analysis,conversion_plan['mapping']))
            from converter.transforms.repair import drop_broken_meshes
            diagnostics.extend(drop_broken_meshes(package))
            diagnostics.extend(make_store_icons(package, progress, cancel))
            diagnostics.extend(write_mod_previews(root, package, progress, cancel))
            diagnostics.extend(convert_package_textures(package, progress, cancel, workers=texture_workers))
            diagnostics.extend(optimize_lowest_lods(package, progress, cancel, workers=texture_workers))
            from converter.transforms.singles import apply_single_vehicle_mode
            mod_name = translated(mod_info.get('name', source.stem))
            diagnostics.extend(apply_single_vehicle_mode(package, single_vehicles,
                                                         mod_name if isinstance(mod_name, str) else '', cancel))
            from converter.transforms.prune import prune_unused_textures
            diagnostics.extend(prune_unused_textures(package))
            validation = validate_package(package)
            diagnostics.extend(validation['diagnostics'])
            result['validation'] = validation
            manual = any(d['severity'] == 'error' for d in diagnostics)
            category = 'partial' if manual else 'package'
            final = run / category / conversion_plan['mod_id']
            final.parent.mkdir(parents=True)
            shutil.move(str(package), str(final))
            result['package_dir'] = str(final)
            result['status'] = 'partial' if manual else 'success'
            write_json(final / '_metadata' / 'conversion_status.json', {'status': result['status'], 'game_verified': False,
                       'diagnostics': [d for d in diagnostics if d['severity'] == 'error']})
            if mods_dir is not None and result['status'] == 'success':
                from converter.naming import output_folder_name, unique_child
                mods_dir = Path(mods_dir)
                mods_dir.mkdir(parents=True, exist_ok=True)
                target = unique_child(mods_dir, mods_name or output_folder_name(source, mod_name if isinstance(mod_name, str) else ''))
                shutil.move(str(final), str(target))
                relocated = (target, f'{category}/{conversion_plan["mod_id"]}')
                result['package_dir'] = str(target)
                try:
                    final.parent.rmdir()
                except OSError:
                    pass
    except InterruptedError as exc:
        result['status'] = 'cancelled'
        result['diagnostics'].append(diagnostic('CANCELLED', str(exc), severity='info'))
    except Exception as exc:
        import traceback
        result['diagnostics'].append(diagnostic('CONVERSION_FAILED', str(exc), trace=traceback.format_exc(limit=-6)))
    finally:
        if staging.exists():
            shutil.rmtree(staging)
    output_hashes = {p.relative_to(run).as_posix(): sha256(p) for p in run.rglob('*') if p.is_file()}
    if relocated:  # keep the manifest keyed as if the package were still inside the run folder
        output_hashes.update({f'{relocated[1]}/{p.relative_to(relocated[0]).as_posix()}': sha256(p)
                              for p in relocated[0].rglob('*') if p.is_file()})
    manifest = {'tool_version': __version__, 'profile_id': PROFILE_ID, 'input': analysis.get('files', {}),
                'game_root':analysis.get('game_root'),'game_dependencies':analysis.get('game_dependencies',{}),
                'output': output_hashes, 'game_verified': False, 'blender_used': False}
    write_reports(run, result, analysis, conversion_plan, manifest)
    return result
