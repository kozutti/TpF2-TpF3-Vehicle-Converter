"""Convert every TPF2 MOD found in a folder (for example the Steam Workshop content folder).

Each MOD goes through the same pipeline as a single conversion. MODs that share
the folder can lend each other assets: TPF2 merges the res folders of all active
MODs, so an add-on often points at meshes or sounds that only its base MOD
ships. One lazily built file index of the folder serves every MOD of the run.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
import json
import os
import time
import traceback

from converter import __version__
from converter.adapters.game_assets import SiblingIndex
from converter.adapters.tpf2_reader import cancelled, diagnostic, notify
from converter.naming import output_folder_name
from converter.parsers.lua import parse_mod_metadata
from converter.reporting import write_json
from converter.transforms.texture_usage import DEFAULT_TEXTURE_WORKERS, validate_texture_workers

DEFAULT_MOD_WORKERS = 1  # MODs converted at the same time; 1 converts them one after another
MAX_MOD_WORKERS = 64
POOL_LIMIT = 61  # Windows allows at most 61 workers in one process pool
MODS_READY = 'mods_ready'  # successful packages, one folder per MOD, to copy into the game's mods folder
SKIP_FOLDERS = {'texture_cache', '.git', '__pycache__'}


def find_mods(folder):
    """MOD folders directly inside `folder` (or `folder` itself when it is one).

    A MOD folder has mod.lua. Folders without it (previews, caches) are ignored.
    """
    folder = Path(folder)
    if (folder / 'mod.lua').is_file():
        return [folder]
    try:
        children = sorted(p for p in folder.iterdir() if p.is_dir() and not p.is_symlink() and p.name not in SKIP_FOLDERS)
    except OSError as exc:
        raise ValueError(f'フォルダーを読めません: {folder} ({exc})') from exc
    return [p for p in children if (p / 'mod.lua').is_file()]


VEHICLE_KINDS = (('rail', b'railVehicle', '鉄道車両'), ('road', b'roadVehicle', '道路車両'),
                 ('water', b'waterVehicle', '船'), ('air', b'airVehicle', '航空機'))
CONFIG_LABELS = {'street': '道路', 'street_type': '道路', 'street_template': '道路', 'track': '線路', 'bridge': '橋',
                 'tunnel': 'トンネル', 'game_script': 'スクリプト', 'cargo': '貨物', 'climate': '気候',
                 'ground_texture': '地面テクスチャ', 'terrain_material': '地形', 'names': '名前リスト',
                 'economy': '経済', 'sound_set': 'サウンド定義', 'model_script': 'スクリプト',
                 'tree': '植物', 'railroad_crossing': '踏切', 'traffic_light': '信号機', 'edge_decoration': '道路・線路の装飾'}
SUPPORT_FOLDERS = {'multiple_unit', 'sound_set'}  # belong to vehicles; not a reason to treat the MOD as something else


def classify_mod(mod):
    """What a TPF2 MOD folder contains, read without running any of its code.

    Only rail vehicles can be converted. Script MODs, buildings, roads, trees,
    road vehicles and ships are reported as not applicable, with the reason.
    Returns {'supported', 'rail_models', 'contents', 'reason'}; `contents` lists
    what the converter will not touch (also for MODs that do contain rail vehicles).
    """
    mod = Path(mod)
    res = mod / 'res'
    counts = {key: 0 for key, _, _ in VEHICLE_KINDS}
    other_models = 0
    models = res / 'models' / 'model'
    if models.is_dir():
        for path in models.rglob('*.mdl'):
            try:
                head = path.read_bytes()
            except OSError:
                continue
            kinds = [key for key, token, _ in VEHICLE_KINDS if token in head]
            for key in kinds:
                counts[key] += 1
            if not kinds:
                other_models += 1
    contents = []
    for key, _, label in VEHICLE_KINDS[1:]:
        if counts[key]:
            contents.append(f'{label} {counts[key]}')
    if other_models:
        contents.append(f'車両以外のモデル {other_models}')
    if (res / 'construction').is_dir():
        contents.append('建物・施設')
    if (res / 'scripts').is_dir() or (res / 'config' / 'game_script').is_dir():
        contents.append('スクリプト')
    config = res / 'config'
    if config.is_dir():
        for child in sorted(config.iterdir()):
            if child.is_dir() and child.name not in SUPPORT_FOLDERS:
                label = CONFIG_LABELS.get(child.name, f'設定 {child.name}')
                if label not in contents:
                    contents.append(label)
    supported = counts['rail'] > 0
    if supported:
        reason = ''
    elif contents:
        reason = '対象外: ' + '、'.join(contents) + '（変換できる鉄道車両がありません）'
    else:
        reason = '対象外: 変換できる鉄道車両がありません（スクリプトや設定のみ）'
    return {'supported': supported, 'rail_models': counts['rail'], 'contents': contents, 'reason': reason}


def has_vehicle_models(mod):
    return classify_mod(mod)['supported']


def mod_title(mod):
    """Display name from mod.lua / strings.lua when they can be read without running code."""
    try:
        info = parse_mod_metadata((Path(mod) / 'mod.lua').read_text('utf-8-sig'), 'mod.lua').get('info', {})
        name = info.get('name', '')
        strings = Path(mod) / 'strings.lua'
        if strings.is_file() and isinstance(name, str):
            from converter.parsers.lua import parse
            text = strings.read_text('utf-8-sig')
            try:
                table = parse(text, 'strings.lua')
            except ValueError:
                from converter.parsers import lua_sandbox
                table = lua_sandbox.evaluate(text, 'strings.lua', lambda module: None)
            for language in ('ja', 'en'):
                translated = table.get(language, {}).get(name) if isinstance(table.get(language), dict) else None
                if translated:
                    return translated
        return name if isinstance(name, str) and name else Path(mod).name
    except (OSError, ValueError, UnicodeError):
        return Path(mod).name


def summarize(result):
    counts = {}
    for entry in result.get('diagnostics', []):
        if isinstance(entry, dict):
            counts[entry.get('severity', '')] = counts.get(entry.get('severity', ''), 0) + 1
    return counts


def validate_mod_workers(workers):
    if type(workers) is not int or not 1 <= workers <= MAX_MOD_WORKERS:
        raise ValueError(f'MOD並列数は1～{MAX_MOD_WORKERS}を指定してください。')


def _failed_row(mod, title, reason, trace=None):
    row = {'folder': Path(mod).name, 'title': title, 'source': str(mod), 'contents': [], 'status': 'failed', 'reason': reason}
    if trace:
        row['trace'] = trace
    return row


def _convert_mod(mod, title, mods_name, run, mods_ready, game_root, single_vehicles, siblings, texture_workers, progress, cancel):
    """One MOD of a run. Returns (result row, True when the run was cancelled while converting it)."""
    from converter import pipeline
    kind = classify_mod(mod)
    row = {'folder': mod.name, 'title': title, 'source': str(mod), 'contents': kind['contents']}
    if not kind['supported']:
        row.update(status='skipped', reason=kind['reason'])
        return row, False
    row['rail_models'] = kind['rail_models']
    try:
        result = pipeline.convert(mod, run / mod.name, progress=progress, cancel=cancel, game_root=game_root,
                                  single_vehicles=single_vehicles, siblings=siblings, texture_workers=texture_workers,
                                  mods_dir=mods_ready, mods_name=mods_name or output_folder_name(mod, title))
        row.update(status=result.get('status'), package_dir=result.get('package_dir'), run_dir=result.get('run_dir'),
                   diagnostics=summarize(result), reference_mods=result.get('reference_mods', {}),
                   errors=[{'code': d.get('code'), 'file': d.get('file'), 'message': d.get('message')}
                           for d in result.get('diagnostics', [])
                           if isinstance(d, dict) and d.get('severity') in {'error', 'fatal'}][:20])
    except InterruptedError:
        row.update(status='cancelled')
        return row, True
    except Exception as exc:  # one broken MOD must not stop the run
        row.update(status='failed', reason=f'{type(exc).__name__}: {exc}', trace=traceback.format_exc(limit=3))
    return row, False


def unique_mods_names(mods, titles):
    """Output folder name per MOD, distinct within the run (Windows folder names ignore case).

    Parallel workers cannot take turns picking a free name, so the names are settled up front.
    """
    taken, names = set(), []
    for mod, title in zip(mods, titles):
        name = output_folder_name(mod, title)
        candidate, index = name, 2
        while candidate.casefold() in taken:
            candidate, index = f'{name}_{index}', index + 1
        taken.add(candidate.casefold())
        names.append(candidate)
    return names


# State of one MOD worker process, set by its initializer.
_mod_queue = None
_mod_stop = None
_mod_siblings = None


def _init_mod_worker(queue, stop, sibling_folder):
    global _mod_queue, _mod_stop, _mod_siblings
    _mod_queue, _mod_stop = queue, stop
    # Built lazily and kept for the MODs this process converts; an index cannot be shared between processes cheaply.
    _mod_siblings = SiblingIndex(sibling_folder, stop) if sibling_folder is not None else None


def _mod_task(index, mod, title, mods_name, run, mods_ready, game_root, single_vehicles, texture_workers):
    def progress(event):
        _mod_queue.put((index, event))
    _mod_queue.put((index, None))  # this MOD has started
    row, interrupted = _convert_mod(Path(mod), title, mods_name, Path(run), Path(mods_ready), game_root, single_vehicles,
                                    _mod_siblings, texture_workers, progress, _mod_stop)
    return index, row, interrupted


def pool_sizes(workers):
    """One pool, or two balanced ones above the Windows limit of 61 workers per pool."""
    return [workers] if workers <= POOL_LIMIT else [workers // 2, workers - workers // 2]


def _convert_in_parallel(mods, titles, names, workers, run, mods_ready, parent, progress, cancel, game_root, single_vehicles,
                         search_siblings, texture_workers):
    """Convert the MODs in `workers` processes. Returns (rows in MOD order, whether the run was cancelled)."""
    import multiprocessing
    from concurrent.futures import ProcessPoolExecutor, wait, FIRST_COMPLETED
    # Spawn works with Windows, GUI worker threads, and the frozen executable.
    context = multiprocessing.get_context('spawn')
    stop, events = context.Event(), context.SimpleQueue()
    total, started, submitted = len(mods), 0, 0
    sizes = pool_sizes(workers)
    active = [0] * len(sizes)
    rows, pending, was_cancelled = {}, {}, False

    def drain():
        nonlocal started
        while not events.empty():
            index, event = events.get()
            if event is None:
                started += 1
                notify(progress, 'batch', started, total, f'{mods[index].name} {titles[index]}')
            elif progress:
                message = event.get('message')
                progress({**event, 'message': f'[{mods[index].name}] {message}' if message else message})

    pools = [ProcessPoolExecutor(max_workers=size, mp_context=context, initializer=_init_mod_worker,
                                 initargs=(events, stop, parent if search_siblings else None)) for size in sizes]
    try:
        while pending or (submitted < total and not was_cancelled):
            if not was_cancelled and cancelled(cancel):
                was_cancelled = True
                stop.set()
            # Keep the queue bounded so cancellation does not drain a long backlog.
            for pool_index, pool in enumerate(pools):
                while submitted < total and active[pool_index] < sizes[pool_index] and not was_cancelled:
                    try:
                        future = pool.submit(_mod_task, submitted, str(mods[submitted]), titles[submitted], names[submitted], str(run),
                                             str(mods_ready), game_root, single_vehicles, texture_workers)
                        pending[future] = (submitted, pool_index)
                        active[pool_index] += 1
                    except Exception as exc:  # the process pool broke: the MODs not yet started fail
                        rows[submitted] = _failed_row(mods[submitted], titles[submitted], f'{type(exc).__name__}: {exc}')
                    submitted += 1
            done, _ = wait(pending, timeout=0.1, return_when=FIRST_COMPLETED)
            drain()
            for future in done:
                index, pool_index = pending.pop(future)
                active[pool_index] -= 1
                try:
                    _, rows[index], interrupted = future.result()
                except Exception as exc:  # a worker process that died takes only its own MOD with it
                    rows[index] = _failed_row(mods[index], titles[index], f'{type(exc).__name__}: {exc}')
                    interrupted = False
                was_cancelled = was_cancelled or interrupted
        drain()
    finally:
        stop.set()
        for pool in pools:
            pool.shutdown(wait=False, cancel_futures=True)
        # Keep emptying the queue: a worker blocked on a full pipe could not finish otherwise.
        while any(not future.done() for future in pending):
            drain()
            time.sleep(0.05)
        for pool in pools:
            pool.shutdown(wait=True)
    return [rows[index] for index in sorted(rows)], was_cancelled


def batch(folder, output, progress=None, cancel=None, game_root=None, single_vehicles='hide', search_siblings=True,
          only=None, texture_workers=DEFAULT_TEXTURE_WORKERS, mod_workers=DEFAULT_MOD_WORKERS):
    """Convert all vehicle MODs in `folder`. Returns a result dict shaped like convert()'s.

    `only` limits the run to MOD folder names. A failing MOD never stops the others.
    With `mod_workers` > 1, that many MODs are converted at the same time in separate processes, each with its own
    `texture_workers` texture processes: the number of processes is the product of the two.
    """
    validate_texture_workers(texture_workers)
    validate_mod_workers(mod_workers)
    folder, output = Path(folder).resolve(), Path(output).resolve()
    if output == folder or output.is_relative_to(folder):
        raise ValueError('変換元のフォルダー内には出力できません。別のフォルダーを指定してください。')
    mods = find_mods(folder)
    if only:
        wanted = {name.strip().casefold() for name in only}
        mods = [m for m in mods if m.name.casefold() in wanted]
    if not mods:
        raise ValueError('mod.luaを持つMODフォルダーが見つかりません。')
    run = output / datetime.now().strftime('batch_%Y%m%d_%H%M%S')
    run.mkdir(parents=True, exist_ok=False)
    mods_ready = run / MODS_READY
    parent = folder.parent if (folder / 'mod.lua').is_file() else folder
    workers = min(mod_workers, len(mods))
    status = 'success'
    if workers > 1:
        processes, cores = workers * texture_workers, os.cpu_count() or 1
        if processes > cores:  # not clamped: the product is the user's choice, but make it visible
            notify(progress, 'batch', 0, len(mods), f'注意: MOD {workers}件 × 画像 {texture_workers} で、最大 {processes} プロセスを同時に使います'
                                                  f'（CPUの論理コア数: {cores}）。CPUやメモリが足りないときは並列数を減らしてください。')
        titles = [mod_title(mod) for mod in mods]
        rows, was_cancelled = _convert_in_parallel(
            mods, titles, unique_mods_names(mods, titles), workers, run, mods_ready, parent, progress, cancel, game_root,
            single_vehicles, search_siblings, texture_workers)
        if was_cancelled:
            status = 'cancelled'
    else:
        siblings = SiblingIndex(parent, cancel) if search_siblings else None
        rows = []
        for index, mod in enumerate(mods, 1):
            if cancelled(cancel):
                status = 'cancelled'
                break
            title = mod_title(mod)
            notify(progress, 'batch', index, len(mods), f'{mod.name} {title}')
            row, interrupted = _convert_mod(mod, title, None, run, mods_ready, game_root, single_vehicles, siblings,
                                            texture_workers, progress, cancel)
            rows.append(row)
            if interrupted:
                status = 'cancelled'
                break
    converted = [r for r in rows if r['status'] in {'success', 'partial'}]
    if status != 'cancelled':
        if any(r['status'] in {'partial', 'failed'} for r in rows):
            status = 'partial'
        if rows and not converted:
            status = 'failed' if any(r['status'] == 'failed' for r in rows) else 'partial'
    summary = {'tool_version': __version__, 'source_folder': str(folder), 'output': str(run), 'status': status,
               'mods_ready': str(mods_ready) if mods_ready.is_dir() else None,
               'single_vehicles': single_vehicles, 'sibling_search': bool(search_siblings), 'texture_workers': texture_workers,
               'mod_workers': workers,
               'counts': {k: sum(1 for r in rows if r['status'] == k) for k in sorted({r['status'] for r in rows})},
               'mods': rows, 'game_verified': False}
    write_json(run / 'batch_summary.json', summary)
    (run / 'batch_summary.md').write_text(render_markdown(summary), encoding='utf-8')
    return {'status': status, 'run_dir': str(run), 'package_dir': None, 'batch': summary,
            'diagnostics': [diagnostic('BATCH_SUMMARY', f'{len(converted)}/{len(mods)} 件を変換しました。一覧: {run / "batch_summary.md"}', severity='info')]
                           + [diagnostic('BATCH_MOD_PROBLEM', f'{r["folder"]}（{r["title"]}）: {r["status"]} {r.get("reason", "")}'.strip(),
                                         r['folder'], severity='warning') for r in rows if r['status'] in {'partial', 'failed'}]}


def render_markdown(summary):
    lines = ['# 一括変換の結果', '',
             f'- 元フォルダー: `{summary["source_folder"]}`', f'- 状態: {summary["status"]}',
             *([f'- ゲームの`mods`フォルダーへそのままコピーできるフォルダー: `{summary["mods_ready"]}`'] if summary.get('mods_ready') else []),
             f'- 単体車両の扱い: {summary["single_vehicles"]}',
             f'- 他MODからの資産検索: {"あり" if summary["sibling_search"] else "なし"}',
             *([f'- MODの並列数: {summary["mod_workers"]}'] if summary.get('mod_workers', 1) > 1 else []),
             '- 件数: ' + '、'.join(f'{k} {v}' for k, v in summary['counts'].items()), '',
             '| フォルダー | MOD名 | 結果 | 取り込み元 | エラー | 備考 |', '| --- | --- | --- | --- | --- | --- |']
    for row in summary['mods']:
        refs = '、'.join(f'{name}({count})' for name, count in row.get('reference_mods', {}).items()) or '-'
        diag = row.get('diagnostics', {})
        errors = diag.get('error', 0)
        note = row.get('reason') or ''
        if row.get('package_dir'):
            note = (note + ' ' + f'`{row["package_dir"]}`').strip()
        if row['status'] != 'skipped' and row.get('contents'):
            note = (note + ' 変換対象外の同梱物: ' + '、'.join(row['contents'])).strip()
        lines.append(f'| {row["folder"]} | {str(row["title"]).replace("|", "/")} | {row["status"]} | {refs} | {errors} | {note} |')
    lines += ['', '`partial`は要手動対応の項目が残っています。各出力フォルダーの`reports/conversion_report.md`を確認してください。',
              '他のMODから取り込んだ資産を含む出力は、個人利用にとどめ、再配布しないでください。']
    return '\n'.join(lines) + '\n'


STATUS_TEXT = {'success': '成功', 'partial': '要手動対応', 'failed': '失敗', 'skipped': '対象外', 'cancelled': '取消し'}


def format_table(summary):
    """Compact result list for a console or log window."""
    lines = [f'{"結果":<8}{"フォルダー":<14}MOD名']
    for row in summary['mods']:
        extra = row.get('reason') or ''
        if row['status'] in {'success', 'partial'}:
            errors = row.get('diagnostics', {}).get('error', 0)
            refs = '、'.join(f'{n}から{c}件' for n, c in row.get('reference_mods', {}).items())
            extra = '、'.join(x for x in (f'エラー {errors}件' if errors else '', f'取り込み: {refs}' if refs else '') if x)
        lines.append(f'{STATUS_TEXT.get(row["status"], row["status"]):<8}{row["folder"]:<14}{row["title"]}' + (f'  — {extra}' if extra else ''))
    counts = '、'.join(f'{STATUS_TEXT.get(k, k)} {v}' for k, v in summary['counts'].items())
    return '\n'.join(lines + ['', f'合計: {counts}'])
