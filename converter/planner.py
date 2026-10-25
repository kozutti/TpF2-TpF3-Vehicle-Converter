"""A plan contains evidence and explicit manual items, never implied success."""
import hashlib
import posixpath
import re
from converter.adapters.tpf3_writer import write_definition, is_multiple_unit, multiple_unit_references
from converter.adapters.tpf2_reader import diagnostic
from converter.resolver import literal_digest, output_path, portable_path, resource_refs
from converter.transforms.icons import ICON_ROOTS, icon_mapping

PROFILE_ID = 'tpf3-wiki-2026-10-07'


def unused_test_locomotives(analysis):
    """Exclude the proven leftover box locomotive, never a consist/group dependency.

    A known MOD ships an unused placeholder box locomotive with no cargo or
    icons. Treating every railVehicle as a purchasable car exposes that box.
    Ordinary locomotives can have no cargo, so that alone is not an exclusion.
    """
    linked = {ref.get('resolved') for ref in analysis.get('references', [])}
    result = set()
    for path in analysis.get('vehicles', []):
        if literal_digest(path.casefold()) != '121d5c7c9c249f3c' or path in linked:
            continue
        model = analysis['parsed'][path]
        meta = model.get('metadata', {})
        transport = meta.get('transportVehicle', {})
        sound = meta.get('railVehicle', {}).get('soundSet', {})
        if literal_digest(str(meta.get('description', {}).get('name'))) != 'db816dca758de457' or not isinstance(sound, dict) or sound.get('name') != 'empty':
            continue
        if any(transport.get(field) for field in ('compartmentsList', 'compartments', 'capacities')):
            continue
        refs = {ref.casefold() for ref, _ in resource_refs(model)}
        if any(literal_digest(ref) == '1c61968d12f9fd11' for ref in refs):
            result.add(path)
    return result


def build_plan(analysis):
    digest = hashlib.sha256(('\n'.join(f'{p}:{d["sha256"]}' for p, d in sorted(analysis['files'].items()))).encode()).hexdigest()
    from converter.naming import mod_id_core
    mod_id = f'converted_{mod_id_core(analysis["source"])}'  # no content hash: a re-conversion replaces the earlier copy
    mapping = {p: portable_path(output_path(p, mod_id)) for p in analysis['files'] if p.startswith('res/')}
    for path in [p for p in mapping if p.startswith('res/config/sound_set/') and p.endswith('.lua')]:
        del mapping[path]  # converted by the sound step (or dropped when no vehicle uses it)
    excluded = []
    # Only rail vehicles are converted. Props, buildings, road vehicles and ships in the same MOD are left out
    # (their meshes and materials then count as unused and are left out too).
    unused_helpers = unused_test_locomotives(analysis)
    for path in unused_helpers:
        mapping.pop(path, None)
    rail = set(analysis.get('vehicles', [])) - unused_helpers
    # models a rail vehicle names (the containers on a freight car) are converted along with it
    edges = {}
    for reference in analysis.get('references', []):
        if reference.get('resolved'):
            edges.setdefault(reference['file'], []).append(reference['resolved'])
    carried, frontier = set(), list(rail)
    while frontier:
        for target in edges.get(frontier.pop(), []):
            if target.endswith('.mdl') and target in analysis['parsed'] and target not in rail and target not in carried:
                carried.add(target)
                frontier.append(target)
    skipped = [p for p in mapping if p.endswith('.mdl') and p in analysis['parsed'] and p not in rail and p not in carried]
    for path in skipped:
        del mapping[path]
    for path in [p for p in mapping if p.lower().endswith('.lua') and p not in analysis['parsed'] and not p.startswith('res/config/sound_set/')]:
        del mapping[path]  # script files (res/scripts, libraries) are code, not vehicle resources
    reachable = set(analysis.get('reachable', []))
    pending = list(carried)
    while pending:
        current = pending.pop()
        if current not in reachable:
            reachable.add(current)
        for target in edges.get(current, []):
            if target not in reachable:
                reachable.add(target)
                pending.append(target)
    for path in list(mapping):
        asset = path[:-5] if path.endswith('.msh.blob') else path
        if asset.endswith(('.mtl', '.msh', '.ani')) and asset not in reachable:
            excluded.append(path)
            del mapping[path]
    dropped_units = []
    for path in [p for p in mapping if is_multiple_unit(p) and p in analysis['parsed']]:
        # Model names are written relative to the .mu.lua and may not use "../",
        # so the file goes into the common directory of the models it lists.
        targets = [mapping.get(ref) for ref in multiple_unit_references(analysis['parsed'][path], path, analysis['files'])]
        if None in targets:
            # TPF3 replaces a missing car with a placeholder that can never be bought, which hides the whole consist
            del mapping[path]
            dropped_units.append(path)
            continue
        if targets and None not in targets:
            name = posixpath.basename(mapping[path])
            destination = posixpath.join(posixpath.commonpath([posixpath.dirname(t) for t in targets]), name)
            taken = {value for key, value in mapping.items() if key != path}
            if destination in taken:
                # same file name in two folders of the source MOD: keep both, told apart by a short hash of the source path
                stem, dot, extension = name.partition('.')
                destination = posixpath.join(posixpath.dirname(destination),
                                             f'{stem}_{hashlib.sha256(path.encode()).hexdigest()[:6]}{dot}{extension}')
            mapping[path] = destination
    diagnostics = list(analysis['diagnostics'])
    for path in sorted(unused_helpers):
        diagnostics.append(diagnostic('UNUSED_TEST_LOCOMOTIVE_SKIPPED',
            '編成・グループから参照されない箱型のテスト車両を出力しません。実車・編成は保持します。',
            path, severity='info'))
    for path in dropped_units:
        diagnostics.append(diagnostic('MULTIPLE_UNIT_DROPPED', f'編成 {path} は、MODに存在しない車両を含むため出力しません。', path, severity='warning'))
    if skipped:
        diagnostics.append(diagnostic('NON_RAIL_MODELS_SKIPPED',
            f'鉄道車両ではないモデル {len(skipped)} 個は変換しません（例: {skipped[0]}）。', skipped[0], severity='info'))
    icons = icon_mapping(analysis['files'], mapping, diagnostics)
    for path in [p for p in mapping if p.casefold().startswith(tuple(ICON_ROOTS)) and p not in icons]:
        del mapping[path]
    mapping.update(icons)
    destinations = {}
    for path, target in mapping.items():
        if target in destinations:
            diagnostics.append(diagnostic('OUTPUT_PATH_COLLISION', f'{path}と{destinations[target]}の出力先が重複します。', path))
        destinations[target] = path
    per_file = {}
    for path, data in analysis['parsed'].items():
        if path in mapping and path.endswith(('.mdl', '.mtl', '.ani')):
            per_file[path] = []
            write_definition(data, path, analysis['files'], mapping, mod_id, per_file[path], analysis['parsed'],
                         analysis.get('game_soundsets'))
            diagnostics.extend(per_file[path])
    items = []
    for p in mapping:
        suffix = p.rsplit('.', 1)[-1]
        route = 'PYTHON' if suffix in {'mdl', 'mtl', 'lua'} else 'DIRECT'
        if suffix in {'mdl', 'mtl', 'msh', 'ani', 'lua'} and p not in analysis['parsed']:
            route = 'MANUAL'
        if any(d['severity'] == 'error' for d in per_file.get(p, [])):
            route = 'MANUAL'
        items.append({'rule_id': f'tpf2_{suffix}_to_tpf3_v1', 'source': p, 'target': mapping[p],
                      'route': route, 'evidence': '2026-10-07公式資料と同梱参考MOD',
                      'validation': '再解析、参照解決、blob境界、動作階層の検査'})
    return {'schema_version': 1, 'profile_id': PROFILE_ID, 'mod_id': mod_id,
            'input_hash': digest, 'source': analysis['source'], 'vehicle_count': analysis['vehicle_count'] - len(unused_helpers),
            'mapping': mapping, 'excluded_unused_assets': excluded, 'items': items, 'diagnostics': diagnostics,
            'status': 'partial' if any(d['severity'] == 'error' for d in diagnostics) else 'planned',
            'game_verified': False}
