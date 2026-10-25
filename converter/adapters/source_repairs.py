"""Narrow, evidence-checked corrections for known malformed source resources.

The original files are never written. Unknown malformed numbers remain errors. A known resource is recognised by the
digest of its path or text, and the correction's target is looked up by digest among the files that exist, so no
third-party resource is named here.
"""
import math
import re

from converter.parsers.lua import LuaParseError, _Parser, parse
from converter.resolver import literal_digest as _digest

# Seat position written with a stray exponent: model file, the bad number, and the mesh of the seats around it.
_SEAT_FILES = frozenset({'2fd02dc403d22a6c'})
_SEAT_BAD_NUMBERS = frozenset({'e203984d338981ca'})
_SEAT_MESHES = frozenset({'73ac0c07309d2f63'})
_STRAY_EXPONENT = re.compile(r'-[0-9]+\.[0-9]+E[0-9]+')
# Sound set name without its folder: case-folded model files, and old name -> name with the folder.
_SOUND_MODELS = frozenset({'355990c640d4f924', '4b5fe71a7ae23367', '55f8f8a21e7153c2', '66c4a1c787d5968e',
                           '716ad16617ddcb70', '7d41bf24cb1fae7d', 'aafa5c694f456941', 'bc7ba3cffe26b208',
                           'de54e2af9c1e513d', 'e7d8bb98553d4a51'})
_SOUND_RENAMES = {'8bb8856fa1d992c6': '885479f7fdff1b6b', 'fa9f2d45c195f0cf': 'c63cb5c258878096'}
# Container references to a folder that no longer exists: group model, old reference -> reference that exists.
_CONTAINER_MODELS = frozenset({'47af99fc8eeb9426'})
_CONTAINER_RENAMES = {'41a6e404b79a0426': '8f7974ad35c8dc26', 'aac4b546a9dcf1c1': '0fc641fa53bfcbad',
                      'eb30aedaee950311': 'e830c8d92e13b642'}
# Doubled last element of a transformation matrix: case-folded model file and the mesh of the node.
_MATRIX_MODELS = frozenset({'e275dde99a9a6222'})
_MATRIX_MESHES = frozenset({'f7b6c1ac66bf458d'})
# Horn sample reference that misses a separator: old reference -> reference that exists.
_HORN_RENAMES = {'3929ca4df564e089': '30c4064b69bbab40', 'a488b6f28738a9a3': 'f2a3c1eba4c9104d'}
# Cargo type with a wrong letter case: model file and the wrong spelling.
_CARGO_MODELS = frozenset({'35a767b778645331'})
_CARGO_BAD_TYPES = frozenset({'03500b1c8de3f003'})


def repair_model_references(model, filename, files):
    """Correct known source typos, with resource/field guards."""
    if _digest(filename.casefold()) in _SOUND_MODELS and isinstance(model, dict):
        metadata = model.get('metadata', {})
        rail = metadata.get('railVehicle', {}) if isinstance(metadata, dict) else {}
        sound = rail.get('soundSet', {}) if isinstance(rail, dict) else {}
        old = sound.get('name') if isinstance(sound, dict) else None
        wanted = _SOUND_RENAMES.get(_digest(old)) if isinstance(old, str) else None
        if wanted and 'res/config/sound_set/' + old + '.lua' not in files:
            prefix = 'res/config/sound_set/'
            new = next((path[len(prefix):-4] for path in files if path.startswith(prefix) and path.endswith('.lua')
                        and _digest(path[len(prefix):-4]) == wanted), None)
            if new:
                sound['name'] = new
                return [{'severity': 'warning', 'code': 'SOURCE_REFERENCE_REPAIRED', 'file': filename,
                    'message': f'音声名 {old} を、元MODに実在する {new} に補いました。元MODは変更していません。',
                    'original': old, 'replacement': new, 'location': '/metadata/railVehicle/soundSet/name'}]
        return []
    if _digest(filename) in _CONTAINER_MODELS and isinstance(model, dict):
        from converter.resolver import array, resolve
        repairs = []
        metadata = model.get('metadata', {})
        provider = metadata.get('cargoSlotProvider', {}) if isinstance(metadata, dict) else {}
        slots = array(provider.get('slots', {})) if isinstance(provider, dict) else []
        prefix = 'res/models/model/'
        for index, slot in enumerate(slots, 1):
            models = slot.get('models', {}) if isinstance(slot, dict) else {}
            if not isinstance(models, dict):
                continue
            for key, old in list(models.items()):
                wanted = _CONTAINER_RENAMES.get(_digest(old)) if isinstance(old, str) else None
                if not wanted:
                    continue
                new = next((path[len(prefix):].casefold() for path in files if path.startswith(prefix)
                            and _digest(path[len(prefix):].casefold()) == wanted), None)
                if new and resolve(old, filename, files) is None and resolve(new, filename, files) is not None:
                    models[key] = new
                    repairs.append({'severity': 'warning', 'code': 'SOURCE_REFERENCE_REPAIRED', 'file': filename,
                        'message': f'編成モデルに残る旧コンテナ参照 {old} を実在する {new} に修正しました。元MODは変更していません。',
                        'original': old, 'replacement': new,
                        'location': f'/metadata/cargoSlotProvider/slots/{index}/models/{key}'})
        return repairs
    if not filename.startswith('res/models/model/vehicle/train/') or not isinstance(model, dict):
        return []
    diagnostics = []
    metadata = model.get('metadata', {})
    if not isinstance(metadata, dict):
        return []
    if _digest(filename.casefold()) in _MATRIX_MODELS:
        try:
            node = model['lods'][1]['node']['children'][7]
            matrix = node.get('transf', {})
            mesh = node.get('mesh')
            if node.get('name') == 'Pedal_L' and isinstance(mesh, str) and _digest(mesh) in _MATRIX_MESHES \
                    and isinstance(matrix, dict) and set(matrix) == set(range(1, 18)) \
                    and all(type(v) in (int, float) and math.isfinite(v) for v in matrix.values()) \
                    and [matrix[i] for i in (4, 8, 12, 16, 17)] == [0, 0, 0, 1, 1]:
                del matrix[17]
                diagnostics.append({'severity': 'warning', 'code': 'SOURCE_MATRIX_REPAIRED', 'file': filename,
                    'message': 'Pedal_Lの変換行列にある重複した末尾の1（17番目）を除去しました。元の16要素は保持し、元MODは変更していません。',
                    'original_length': 17, 'replacement_length': 16,
                    'location': '/lods/1/node/children/7/transf'})
        except (KeyError, TypeError, AttributeError):
            pass
    rail = metadata.get('railVehicle', {})
    sound = rail.get('soundSet', {}) if isinstance(rail, dict) else {}
    horn = sound.get('horn') if isinstance(sound, dict) else None
    wanted = _HORN_RENAMES.get(_digest(horn)) if isinstance(horn, str) else None
    if wanted:
        prefix = 'res/audio/effects/'
        new = next((path[len(prefix):] for path in files if path.startswith(prefix)
                    and _digest(path[len(prefix):]) == wanted), None)
        if new:
            sound['horn'] = new
            diagnostics.append({'severity': 'warning', 'code': 'SOURCE_REFERENCE_REPAIRED', 'file': filename,
                'message': f'警笛の参照先 {horn} を元MODに実在する {new} に修正しました。元MODは変更していません。',
                'original': horn, 'replacement': new, 'location': '/metadata/railVehicle/soundSet/horn'})
    if _digest(filename) in _CARGO_MODELS:
        from converter.resolver import walk
        for key, item, at in walk(metadata.get('transportVehicle', {})):
            if key == 'type' and isinstance(item, str) and _digest(item) in _CARGO_BAD_TYPES \
                    and re.fullmatch(r'/compartmentsList/\d+/loadConfigs/\d+/cargoEntries/\d+/type', at):
                current = metadata['transportVehicle']
                for part in at.strip('/').split('/')[:-1]:
                    current = current[int(part) if part.isdigit() else part]
                current['type'] = 'PASSENGERS'
                diagnostics.append({'severity': 'warning', 'code': 'SOURCE_CARGO_TYPE_REPAIRED', 'file': filename,
                    'message': f'編成定義にある貨物種別 {item} を PASSENGERS に修正しました。元MODは変更していません。',
                    'original': item, 'replacement': 'PASSENGERS', 'location': '/metadata/transportVehicle' + at})
    return diagnostics


def repair_model_text(text, filename):
    """Recover a seat position written with a stray exponent only when adjacent seats prove its row spacing."""
    if _digest(filename) not in _SEAT_FILES:
        return text, []
    found = [m.group() for m in _STRAY_EXPONENT.finditer(text) if _digest(m.group()) in _SEAT_BAD_NUMBERS]
    if len(found) != 1 or text.count(found[0]) != 1:
        return text, []
    bad = found[0]
    good = bad.replace('E', '', 1)
    corrected = text.replace(bad, good, 1)
    try:
        tokens = _Parser(text, filename, lex_only=True).tokens
        offset = text.lstrip('﻿').index(bad)
        if not any(previous.value == '-' and previous.offset == offset and token.kind == 'number'
                   and token.offset == offset + 1 and token.value == bad[1:]
                   for previous, token in zip(tokens, tokens[1:])):
            return text, []
        model = parse(corrected, filename, undefined=[])
        matches = []

        def visit(node):
            if not isinstance(node, dict):
                return
            if node.get('name') == 'seat_14':
                matches.append(node)
            children = node.get('children', {})
            if isinstance(children, dict):
                by_name = {child.get('name'): child for child in children.values() if isinstance(child, dict)}
                if all(name in by_name for name in ('seat_13', 'seat_14', 'seat_15', 'seat_16')):
                    groups.append(by_name)
                for child in children.values():
                    visit(child)

        groups = []
        for lod in model.get('lods', {}).values():
            visit(lod.get('node', {}))
        # A resource variant with different seating must be checked separately.
        if len(matches) != 1 or len(groups) != 1:
            return text, []
        seats = groups[0]
        target = seats['seat_14']
        if not isinstance(target.get('mesh'), str) or _digest(target['mesh']) not in _SEAT_MESHES:
            return text, []
        transforms = []
        for name in ('seat_13', 'seat_14', 'seat_15', 'seat_16'):
            node = seats[name]
            matrix = node.get('transf', {})
            if node.get('mesh') != target['mesh'] or node.get('materials') != target.get('materials'):
                return text, []
            if set(matrix) != set(range(1, 17)) or any(type(v) not in (int, float) or not math.isfinite(v) for v in matrix.values()):
                return text, []
            if any(matrix[i] != target['transf'][i] for i in range(1, 17) if i != 13):
                return text, []
            transforms.append(matrix[13])
        x13, x14, x15, x16 = transforms
        if x14 != float(good) or not math.isclose(x13 - x14, x15 - x16, rel_tol=0, abs_tol=1e-9):
            return text, []
    except (LuaParseError, AttributeError, TypeError, KeyError):
        return text, []
    line = text.count('\n', 0, text.index(bad)) + 1
    return corrected, [{'severity': 'warning', 'code': 'SOURCE_NUMBER_REPAIRED', 'file': filename,
        'message': f'{line}行目のseat_14の座席位置 {bad} を {good} に修正しました。同じメッシュ・向きのseat_13/15/16の座席間隔と一致することを確認しました。元MODは変更していません。',
        'rule_id': 'seat_stray_exponent_v1', 'line': line,
        'original': bad, 'replacement': good,
        'location': 'seat_14/transf/13'}]
