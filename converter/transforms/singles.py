"""How individually purchasable vehicles appear in the TPF3 depot list.

TPF3 lists every vehicle whose `filterTags` match the depot. A model marked
`multipleUnitOnly` in TPF2 is only a part of a multiple unit; TPF3 hides such a
part by giving it empty `filterTags`. The converter keeps these parts visible
so that cars can be bought one by one, folded into one row per MOD by default.

* group - visible vehicles are folded under group rows, per MOD and carrier: one row
          for the cars TPF2 sold on their own (単行車両) and one for the parts that only
          exist inside multiple units (編成の部品). A group is a model without geometry
          whose `filterTags` are empty; each member names it in
          `transportVehicle.groupFileName`, the same layout TPF3 mods use. The row shows the first car with a driver's seat.
          This is the default.
* show  - parts stay visible as a flat list.
* hide  - parts of multiple units get empty `filterTags` (TPF2/TPF3 convention).

In `group` and `hide` mode a real car that a multiple unit names as its group parent (and that TPF2 never sold on its own)
gets empty `filterTags` too, so that it is only the group row and not also a one-car vehicle inside its own group.

In every mode a part that only exists inside multiple units and has no icon at all is
hidden from the list: TPF2 never offered it, the author drew no picture for it, and TPF3
would show a purple tile. The multiple units themselves are not touched.
"""
from __future__ import annotations

from copy import deepcopy
import posixpath
import re
import shutil

from converter.adapters.tpf2_reader import diagnostic
from converter.parsers.lua import parse, dumps
from converter.resolver import array, table

MODES = ('group', 'show', 'hide')
GROUP_PREFIX = 'single_group_'
ICON_SUFFIXES = ('_icon20@2x.tga', '_icon20_cblend@2x.tga', '_icon_small@2x.tga',
                 '_icon_small_cblend@2x.tga', '_store.tga')
IDENTITY = table([1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1])


def _cancelled(cancel):
    return cancel is not None and (cancel.is_set() if hasattr(cancel, 'is_set') else cancel())


def _read_models(content):
    models = {}
    for path in sorted(content.rglob('*.mdl')):
        relative = path.relative_to(content).as_posix()
        if relative.startswith('_assets/'):
            continue
        try:
            data = parse(path.read_text(encoding='utf-8-sig'), filename=relative)
        except (ValueError, UnicodeError, OSError):
            continue
        if isinstance(data, dict) and isinstance(data.get('metadata', {}).get('transportVehicle'), dict):
            models[relative] = data
    return models


def _group_parents(content, models):
    """Models that something names as its group: the parent rows of multiple units and variants.

    A parent must stay a parent. If it were folded into another group it would itself become a
    variant, which TPF3 hides from the list, and every unit grouped under it would disappear.
    """
    parents = set()

    def add(owner_path, name):
        if isinstance(name, str) and name:
            parents.add(posixpath.normpath(name.lstrip('/') if name.startswith('/')
                                           else posixpath.join(posixpath.dirname(owner_path), name)))

    for path in sorted(content.rglob('*.mu.lua')):
        try:
            add(path.relative_to(content).as_posix(), parse(path.read_text(encoding='utf-8-sig'), filename=path.name).get('groupFileName'))
        except (ValueError, UnicodeError, OSError):
            continue
    for path, data in models.items():
        add(path, data['metadata']['transportVehicle'].get('groupFileName'))
    return parents


def _has_icon(content, model_path):
    icons = content / posixpath.dirname(model_path) / 'icons'
    stem = posixpath.splitext(posixpath.basename(model_path))[0]
    return any((icons / f'{stem}{suffix}').is_file() for suffix in ICON_SUFFIXES)


def _has_driver_seat(data):
    provider = data['metadata'].get('seatProvider')
    seats = array(provider.get('seats', {})) if isinstance(provider, dict) else []
    return any(isinstance(seat, dict) and seat.get('crew') for seat in seats)


def _head_first(paths, parts, content):
    """The car that stands for the group: has a driver's seat, then has an icon, then comes first."""
    return sorted(paths, key=lambda p: (not _has_driver_seat(parts[p]), not _has_icon(content, p), p))


def _is_placeholder(data):
    return not array(data['metadata']['transportVehicle'].get('compartments', {}))


def group_placeholder(name, description, members):
    """A geometry-free group parent; stats are borrowed from the first member."""
    first = deepcopy(members[0]['metadata'])
    years = [m['metadata'].get('availability', {}).get('yearFrom', 0) for m in members]
    years = [y for y in years if isinstance(y, (int, float)) and y > 0]
    transport = first['transportVehicle']
    meta = {
        'availability': {'yearFrom': min(years, default=0), 'yearTo': 0},
        'cost': {'price': -1},
        'description': {'name': name, 'description': description},
        'maintenance': {'lifespan': 21915, 'runningCosts': -1},
        'emissions': {'noise': {'score': -1}, 'pollution': {'score': -1}},
        'landVehicle': deepcopy(first.get('landVehicle', {'engines': {}, 'topSpeed': 0, 'weightEmpty': 0,
                                                          'weightMaxPayload': 0, 'friction': .02})),
        'railVehicle': {'config': {'axles': {}, 'fakeBogies': {1: {}}}},
        'transformatorConfig': {'transformator': {'name': '::/vehicle/train/shared/default_train.trf'},
                                'skipFromLod': 0},
        'transportVehicle': {
            'carrier': transport.get('carrier', 'RAIL'),
            'compartments': {}, 'compartmentsList': {},
            'transportModes': deepcopy(transport.get('transportModes', table(['TRAIN', 'ELECTRIC_TRAIN']))),
            'engineTransportModes': deepcopy(transport.get('engineTransportModes', {})),
            'tags': deepcopy(transport.get('tags', {})), 'engineTags': deepcopy(transport.get('engineTags', {})),
            'filterTags': {}, 'multipleUnitOnly': True, 'reversible': True, 'loadSpeed': 1,
            'comfortFactor': .5, 'priceFactor': 1, 'arrivalDelay': 0, 'departureDelay': 0},
    }
    zero = table([0, 0, 0])
    return {'version': 2, 'boundingInfo': {'bbMax': deepcopy(zero), 'bbMin': deepcopy(zero)},
            'collider': {'type': 'BOX', 'transf': deepcopy(IDENTITY), 'params': {'halfExtents': deepcopy(zero)}},
            'lods': {1: {'node': {'name': 'RootNode', 'transf': deepcopy(IDENTITY)},
                         'static': False, 'visibleFrom': 0, 'visibleTo': 0}},
            'metadata': meta}


def _copy_icons(content, member_paths, group_path):
    """Give the group row the icons of the first member that has any (icons sit in an `icons` folder next to a model)."""
    group_stem = posixpath.splitext(posixpath.basename(group_path))[0]
    target = content / posixpath.dirname(group_path) / 'icons'
    for member_path in member_paths:
        member_stem = posixpath.splitext(posixpath.basename(member_path))[0]
        icons = content / posixpath.dirname(member_path) / 'icons'
        found = [(icons / f'{member_stem}{suffix}', suffix) for suffix in ICON_SUFFIXES
                 if (icons / f'{member_stem}{suffix}').is_file()]
        if not found:
            continue
        target.mkdir(parents=True, exist_ok=True)
        for source, suffix in found:
            shutil.copyfile(source, target / f'{group_stem}{suffix}')
        return len(found)
    return 0


def _resolve(owner_path, name):
    """A model path named by `owner_path` (a model or a `.mu.lua`), relative to the content folder."""
    return posixpath.normpath(name.lstrip('/') if name.startswith('/') else posixpath.join(posixpath.dirname(owner_path), name))


def _reference(owner_path, target):
    """How `owner_path` names `target`: TPF3 does not accept a literal `../`, so a deeper owner names it from the content root."""
    relative = posixpath.relpath(target, posixpath.dirname(owner_path))
    return f'/{target}' if relative.startswith('..') else relative


def _unit_cars(content):
    """The models that a multiple unit lists as its cars."""
    cars = set()
    for path in sorted(content.rglob('*.mu.lua')):
        relative = path.relative_to(content).as_posix()
        try:
            unit = parse(path.read_text(encoding='utf-8-sig'), filename=path.name)
        except (ValueError, UnicodeError, OSError):
            continue
        for entry in array(unit.get('vehicles', {})) if isinstance(unit, dict) else []:
            if isinstance(entry, dict) and isinstance(entry.get('name'), str) and entry['name']:
                cars.add(_resolve(relative, entry['name']))
    return cars


def _repoint_group(content, models, old, new):
    """Make every multiple unit and model that names `old` as its group name `new` instead. Returns how many references changed."""
    changed = 0
    pattern = re.compile(r'(groupFileName\s*=\s*")([^"]*)(")')
    for path in sorted(content.rglob('*.mu.lua')):
        relative = path.relative_to(content).as_posix()
        text = path.read_text(encoding='utf-8-sig')

        def replace(match):
            nonlocal changed
            if match.group(2) and _resolve(relative, match.group(2)) == old:
                changed += 1
                return match.group(1) + _reference(relative, new) + match.group(3)
            return match.group(0)
        updated = pattern.sub(replace, text)
        if updated != text:
            path.write_text(updated, encoding='utf-8')
    for path, data in models.items():
        transport = data['metadata']['transportVehicle']
        name = transport.get('groupFileName')
        if isinstance(name, str) and name and _resolve(path, name) == old:
            transport['groupFileName'] = _reference(path, new)
            _write(content / path, data)
            changed += 1
    return changed


def _stand_in_parent(content, path, data, models):
    """Give a multiple unit's group a geometry-free stand-in parent, so that the real car which the unit named as its group can
    be an ordinary part (a model cannot be a group parent with its own row and also a member of the group of parts, because
    `filterTags` decide both). The stand-in takes the car's name, description and icons. None when it cannot be done."""
    stem, folder = posixpath.splitext(posixpath.basename(path))[0], posixpath.dirname(path)
    stand_in = posixpath.join(folder, f'{stem}_group.mdl')
    if stand_in in models or (content / stand_in).exists():
        return None
    description = data['metadata'].get('description') if isinstance(data['metadata'].get('description'), dict) else {}
    text = description.get('description') if isinstance(description.get('description'), str) else ''
    placeholder = group_placeholder(description.get('name') if isinstance(description.get('name'), str) else stem, text, [data])
    _write(content / stand_in, placeholder)
    models[stand_in] = placeholder
    if not _repoint_group(content, models, path, stand_in):
        (content / stand_in).unlink()
        del models[stand_in]
        return None
    _copy_icons(content, [path], stand_in)
    return stand_in


def _write(path, data):
    path.write_text(dumps(data), encoding='utf-8')


def apply_single_vehicle_mode(package, mode='hide', mod_name='', cancel=None):
    """Post-process a written package. Returns diagnostics (all informational)."""
    if mode not in MODES:
        raise ValueError(f'単体車両の扱いは {", ".join(MODES)} のいずれかです: {mode}')
    content = package / 'content'
    models = _read_models(content)
    parents = _group_parents(content, models)
    diagnostics = []
    if mode != 'show':
        # A group parent that is a real car which TPF2 never sold on its own (`multipleUnitOnly`) would be listed in its own
        # group as a one-car vehicle. TPF3 shows a parent with empty `filterTags` as the group row only, as other multiple-unit parents are written, and TPF2 does not sell a multiple-unit-only model on its own either.
        # In `group` mode a parent that is also a car of its unit gets a stand-in parent (see _stand_in_parent), and the car
        # itself becomes a part of the group of parts; any other parent is just a menu model.
        cars = _unit_cars(content) if mode == 'group' else set()
        only_group, moved = 0, 0
        for path in sorted(parents & models.keys()):
            data = models[path]
            transport = data['metadata']['transportVehicle']
            if _is_placeholder(data) or not transport.get('multipleUnitOnly') or not array(transport.get('filterTags', {})):
                continue
            if path in cars and _stand_in_parent(content, path, data, models):
                moved += 1
                continue
            transport['filterTags'] = {}
            _write(content / path, data)
            only_group += 1
        if moved:
            parents = _group_parents(content, models)
            diagnostics.append(diagnostic('GROUP_PARENT_CAR_MOVED_TO_PARTS',
                f'編成のグループの親が、編成の車両そのものだったため、見出し用の親（`_group`）を作り、車両 {moved} 両を部品へ移しました。', severity='info'))
        if only_group:
            diagnostics.append(diagnostic('GROUP_PARENT_CAR_ONLY_GROUP',
                f'編成のグループの親になっている編成専用の車両 {only_group} 両を、グループの行だけにしました（親自身が1両の単車として並ぶのを避けます。編成そのものは買えます）。', severity='info'))
    parts = {p: d for p, d in models.items() if not _is_placeholder(d) and p not in parents}
    imageless = 0
    for path, data in parts.items():
        transport = data['metadata']['transportVehicle']
        if transport.get('multipleUnitOnly') and array(transport.get('filterTags', {})) and not _has_icon(content, path):
            transport['filterTags'] = {}
            _write(content / path, data)
            imageless += 1
    if imageless:
        diagnostics.append(diagnostic('PART_WITHOUT_IMAGE_HIDDEN',
            f'一覧用の画像が1枚もない編成専用の車両 {imageless} 両を車庫の単体一覧から隠しました（TPF2でも一覧に出ません。編成そのものは買えます）。', severity='info'))
    if mode == 'show':
        return diagnostics
    if mode == 'hide':
        hidden = 0
        for path, data in parts.items():
            if _cancelled(cancel):
                raise InterruptedError('変換を取り消しました。')
            transport = data['metadata']['transportVehicle']
            if transport.get('multipleUnitOnly') and array(transport.get('filterTags', {})):
                transport['filterTags'] = {}
                _write(content / path, data)
                hidden += 1
        diagnostics.append(diagnostic('SINGLE_VEHICLES_HIDDEN',
            f'編成専用の車両 {hidden} 両を車庫の単体一覧から隠しました（TPF2と同じ扱い）。', severity='info'))
        return diagnostics
    carriers = {}
    for path, data in parts.items():
        transport = data['metadata']['transportVehicle']
        if not array(transport.get('filterTags', {})) or transport.get('groupFileName'):
            continue  # hidden from the list, or already in a group of its own
        carriers.setdefault((str(transport.get('carrier', 'RAIL')), bool(transport.get('multipleUnitOnly'))), []).append(path)
    made = 0
    several_carriers = len({carrier for carrier, __ in carriers}) > 1
    for (carrier, is_part), paths in sorted(carriers.items()):
        if _cancelled(cancel):
            raise InterruptedError('変換を取り消しました。')
        if len(paths) < 2:
            continue
        folder = posixpath.commonpath([posixpath.dirname(p) for p in paths]) or 'vehicle'
        group_path = posixpath.join(folder, f'{GROUP_PREFIX}{carrier.lower()}{"" if is_part else "_solo"}.mdl')
        if group_path in models:
            continue
        label = '編成の部品' if is_part else '単行車両'
        if several_carriers:
            label = f'{label}・{carrier}'
        title = f'{mod_name} {label}（{len(paths)}両）'.strip()
        ordered = _head_first(paths, parts, content)
        _write(content / group_path, group_placeholder(title, f'{len(paths)}種類の車両を個別に選べます。', [parts[p] for p in ordered]))
        icons = _copy_icons(content, ordered, group_path)
        for path in paths:
            data = parts[path]
            relative = posixpath.relpath(group_path, posixpath.dirname(path))
            data['metadata']['transportVehicle']['groupFileName'] = f'/{group_path}' if relative.startswith('..') else relative
            _write(content / path, data)
        made += 1
        diagnostics.append(diagnostic('SINGLE_VEHICLE_GROUP_CREATED',
            f'{len(paths)}両を「{title}」にまとめました。', group_path, severity='info', icons_copied=icons))
    if not made:
        diagnostics.append(diagnostic('SINGLE_VEHICLE_GROUP_NONE',
            '2両以上の単体車両がないため、まとめるグループは作りませんでした。', severity='info'))
    return diagnostics
