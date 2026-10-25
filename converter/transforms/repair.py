"""Output-package repairs for source data that cannot be loaded, so that one damaged mesh does not sink a whole MOD."""
from pathlib import Path
import hashlib
import json
import math

from converter.adapters.tpf2_reader import diagnostic
from converter.codecs.mesh import validate_mesh
from converter.parsers.lua import LuaParseError, dumps, parse
from converter.resolver import array, literal_digest as _digest, prune_missing_axles, table


def _normalized(reference):
    return reference.replace('\\', '/').lstrip('/').lower() if isinstance(reference, str) else ''


# Known source assets are recognised by the digest of their (normalized) path or name, never spelled out here.
_FACE_PATHS = frozenset({'2d9abc3e9f30c95a'})
_FACE_NAMES = frozenset({'ca3999b1a5ed113d'})
_PLATE_MESH_PATHS = frozenset({'da2e95221bf2eeee'})
_PLATE_MESH_NAMES = frozenset({'32e87625c5161ba6'})
_PLATE_ANIMATION_PATHS = frozenset({'7de6f0d933ebb0ef'})
_PLATE_ANIMATION_NAMES = frozenset({'65f702e329c2ce4a'})
_PLATE_NODES = frozenset({'1613d4771005ca5d', '3477fd0712950d58', '9af5a3dec6013476', 'af99b9f6a8cc80de'})
_PLATE_MATERIALS = {'F': frozenset({'86d5e0664dff4f18', 'c18e820458861fa8'}),
                    'B': frozenset({'52d7d1fa5839e501', 'a68b9204c5a9c9ff'})}
_PLACEHOLDER_NAMES = frozenset({'35682162feceb85c'})


def _find_asset(content, kind, wanted):
    """The converted asset `_assets/<kind>/vehicle/train/<file>` whose case-folded file name has a digest in `wanted`."""
    folder = content / '_assets' / kind / 'vehicle' / 'train'
    if folder.is_dir():
        for path in sorted(folder.iterdir()):
            if path.is_file() and _digest(path.name.casefold()) in wanted:
                return path
    return None


def _fix_known_labels(model):
    """Move the observed label in front of the known face panel (x=9.50).

    Called only after the face binary/layout fingerprint matches. The original
    labels sit at 9.475 in front of a panel at 9.45; the replacement face
    moves that panel forward by 5 cm but reuses the same label coordinates.
    Keep the original 2.5 cm clearance, inside the glass at about 9.582.
    """
    from converter.adapters.bogies import IDENTITY, matrix, multiply, walk
    lods = array(model.get('lods', {}))
    labels = array(model.get('metadata', {}).get('labelList', {}).get('labels', {}))
    changed = 0
    for label in labels:
        if not isinstance(label, dict) or label.get('type') != 'NEXT_STOP':
            continue
        values = array(label.get('transf', {}))
        size = array(label.get('size', {}))
        sign = 1 if len(values) == 16 and values[12] > 0 else -1
        expected = [0, sign, 0, 0, 0, 0, 1, 0, 1, 0, 0, 0,
                    sign * 9.475, -sign * .30, 3.31, 1]
        if (size != [.60, .17] or len(values) != 16
                or any(type(v) not in (int, float) or not math.isfinite(v) or abs(v-e) > 1e-6 for v, e in zip(values, expected))
                or not lods):
            continue
        safe = True
        for lod in lods:
            root = lod.get('node') if isinstance(lod, dict) else None
            if not isinstance(root, dict):
                safe = False; break
            anchors, faces = [], []
            for node, branch in walk(root):
                frame = IDENTITY[:]
                for ancestor in branch:
                    local = matrix(ancestor)
                    if local is None or ancestor.get('animations'):
                        frame = None; break
                    frame = multiply(frame, local)
                if node.get('name') == label.get('childId'):
                    anchors.append(frame)
                if _digest(_normalized(node.get('mesh'))) in _FACE_PATHS:
                    faces.append(frame)
            expected_face = IDENTITY[:]; expected_face[0] = expected_face[5] = sign
            def same(frame, expected):
                return frame is not None and all(abs(v-e) < 1e-6 for v, e in zip(frame, expected))
            if (len(anchors) != 1 or not same(anchors[0], IDENTITY)
                    or len(faces) != 1 or not same(faces[0], expected_face)):
                safe = False; break
        if safe:
            values[12] = sign * 9.525
            # The label origin is its lower-left corner, so its middle sits at z 3.395, 2-3.5 cm above the middle of the
            # face's glass (3.12..3.60) and of the plate (3.232..3.52): lowered 3 cm (seen in game, 2026-10-12).
            values[14] = 3.28
            label['transf'] = table(values)
            changed += 1
    return changed


def _fix_known_backgrounds(model):
    """Keep the four animated destination states in front of the replacement face.

    Only called with the observed face, plate and animation fingerprints.
    The reversed car already translates its plate by +1 cm; retain that
    offset and move outward by -5 cm. Check every LOD before mutating any.
    """
    from converter.adapters.bogies import IDENTITY, matrix, multiply, walk
    moves = []
    direction = None
    for lod in array(model.get('lods', {})):
        root = lod.get('node') if isinstance(lod, dict) else None
        if not isinstance(root, dict):
            return 0
        faces, plates = [], []
        for node, branch in walk(root):
            if (_digest(_normalized(node.get('mesh'))) not in _FACE_PATHS
                    and not _is_plate(node)):
                continue
            parent = IDENTITY[:]
            for ancestor in branch[:-1]:
                local = matrix(ancestor)
                if local is None or ancestor.get('animations'):
                    return 0
                parent = multiply(parent, local)
            if any(abs(v-e) > 1e-6 for v, e in zip(parent, IDENTITY)):
                return 0
            if _is_plate(node):
                plates.append(node)
            else:
                faces.append(node)
        if len(faces) != 1 or faces[0].get('animations'):
            return 0
        face = matrix(faces[0])
        sign = 1 if face is not None and face[0] > 0 else -1
        expected = IDENTITY[:]; expected[0] = expected[5] = sign
        if face is None or any(abs(v-e) > 1e-6 for v, e in zip(face, expected)):
            return 0
        if direction is not None and direction != sign:
            return 0
        direction = sign
        # The source far LOD has no destination plates. Do not add them.
        if not plates:
            continue
        if len(plates) != 4 or {_digest(n['name']) for n in plates} != _PLATE_NODES:
            return 0
        expected[12] = 0 if sign == 1 else .01
        for node in plates:
            values = matrix(node)
            animation = node.get('animations', {})
            forever = animation.get('forever', {}) if isinstance(animation, dict) else {}
            state = {'FF': 'front_forward', 'BB': 'back_backward',
                     'IF': 'inner_forward', 'IB': 'inner_backward'}[node['name'][:2]]
            if sign == -1:
                state = {'FF': 'back_forward', 'BB': 'front_backward',
                         'IF': 'inner_forward', 'IB': 'inner_backward'}[node['name'][:2]]
            visibility = {f'{state}_parts_{mode}': {'type': 'FILE_REF', 'interpolation': 'NEAREST',
                          'params': {'id': f'::/vehicle/shared/ani/{state}_parts_{mode}.ani'}}
                          for mode in ('on', 'off')}
            other_animations = {k: v for k, v in animation.items() if k != 'forever'} if isinstance(animation, dict) else None
            suffix = 'F' if node['name'][1] == 'F' else 'B'
            materials = [_normalized(m) for m in array(node.get('materials', {}))]
            if (_digest(_normalized(node.get('mesh'))) not in _PLATE_MESH_PATHS
                    or len(materials) != 1 or _digest(materials[0]) not in _PLATE_MATERIALS[suffix]
                    or array(node.get('children', {}))
                    or any(k in node for k in ('skin', 'skinWeights', 'bones'))
                    or other_animations not in ({}, visibility) or forever.get('type') != 'FILE_REF'
                    or _digest(_normalized(forever.get('params', {}).get('id'))) not in _PLATE_ANIMATION_PATHS
                    or values is None):
                return 0
            original = all(abs(v-e) < 1e-6 for v, e in zip(values, expected))
            corrected = expected[:]; corrected[12] += sign * .05
            if not original and not all(abs(v-e) < 1e-6 for v, e in zip(values, corrected)):
                return 0
            if original:
                shifted = values[:]; shifted[12] += sign * .05
                moves.append((node, shifted))
    for node, values in moves:
        node['transf'] = table(values)
    return len(moves)


def _is_plate(node):
    name = node.get('name')
    return isinstance(name, str) and _digest(name) in _PLATE_NODES


def _known_plate_assets(content):
    mesh = _find_asset(content, 'mesh', _PLATE_MESH_NAMES)
    animation = _find_asset(content, 'animation', _PLATE_ANIMATION_NAMES)
    if mesh is None or animation is None:
        return False
    try:
        if hashlib.sha256(Path(str(mesh) + '.blob').read_bytes()).hexdigest() != '59e055da07c02d890458e3d5bcd24153752abc54dcf3086d17fe2d15ee93ac09':
            return False
        for path, expected in ((mesh, '075f5511fd456243788cd6a289a8bb44493a10d05a0a4cae32d5efb604cf8696'),
                               (animation, '170b71faac740cf38fd81f7e89e48026f0f2c71d8c4c984ab2896d665e5f1038')):
            data = parse(path.read_text('utf-8-sig'), str(path))
            if hashlib.sha256(json.dumps(data, sort_keys=True, separators=(',', ':')).encode()).hexdigest() != expected:
                return False
    except (LuaParseError, ValueError, UnicodeError, OSError):
        return False
    return True


def repair_known_destination_labels(package) -> list:
    """Repair only the fingerprinted source face, leaving mesh/material data intact."""
    content = Path(package) / 'content'
    mesh = _find_asset(content, 'mesh', _FACE_NAMES)
    if mesh is None:
        return []
    blob = Path(str(mesh) + '.blob')
    if not mesh.is_file() or not blob.is_file():
        return []
    try:
        if hashlib.sha256(blob.read_bytes()).hexdigest() != '486bfcace8bc6c4776a64038c4105f470cbceafb677bf937f3ea51e51d1fa953':
            return []
        definition = parse(mesh.read_text('utf-8-sig'), str(mesh))
        # The observed source layout; changed index/attribute ranges stay untouched.
        if hashlib.sha256(json.dumps(definition, sort_keys=True, separators=(',', ':')).encode()).hexdigest() != 'c86745760b97a66139c91dbd62a3941625fc7dd2d78e83b5e4df786adb47894d':
            return []
    except (LuaParseError, ValueError, UnicodeError, OSError):
        return []
    diagnostics = []
    plates_known = _known_plate_assets(content)
    for model in sorted(content.rglob('*.mdl')):
        try:
            data = parse(model.read_text('utf-8-sig'), str(model))
        except (LuaParseError, ValueError, UnicodeError, OSError):
            continue
        count = _fix_known_labels(data)
        plate_count = _fix_known_backgrounds(data) if plates_known else 0
        if count or plate_count:
            model.write_text(dumps(data), 'utf-8')
        if count:
            diagnostics.append(diagnostic('KNOWN_DESTINATION_LABEL_UNOCCLUDED',
                f'既知の正面メッシュの内側に埋まった行先文字{count}個を5cm前へ、3cm下へ移しました。文字設定・側面表示・メッシュは保持します。',
                model.relative_to(content).as_posix(), severity='info'))
        if plate_count:
            diagnostics.append(diagnostic('KNOWN_DESTINATION_BACKGROUND_UNOCCLUDED',
                f'既知の正面の行先背景板{plate_count}個を外側へ5cm移しました。表示切替・アニメーション・材質・遠景LODは保持します。',
                model.relative_to(content).as_posix(), severity='info'))
    return diagnostics


def collapse_selection_placeholder(package) -> list:
    """Collapse the known off-body placeholder quad without deleting its frames.

    The source quad sits 11.275 m beside the car and can enter selection
    outlines. Some LODs use the same node as a fake-bogie frame: preserve its
    names, transforms, mesh definition and every non-position byte. Both the
    complete layout and blob must match the observed source asset, so another
    author's real geometry with the same filename remains untouched.
    """
    mesh = _find_asset(Path(package) / 'content', 'mesh', _PLACEHOLDER_NAMES)
    if mesh is None:
        return []
    blob = Path(str(mesh) + '.blob')
    if not mesh.is_file() or not blob.is_file() or blob.stat().st_size != 400:
        return []
    try:
        definition = parse(mesh.read_text('utf-8'), str(mesh))
        layout_hash = hashlib.sha256(json.dumps(definition, sort_keys=True,
                                               separators=(',', ':')).encode()).hexdigest()
        raw = blob.read_bytes()
    except (LuaParseError, ValueError, UnicodeError, OSError):
        return []
    if (layout_hash != '73243b6504cdd88a1b6b6c30afa959fbbade33b84edf0357da11bc19887adc63'
            or hashlib.sha256(raw).hexdigest() != '98d9bb9198d075693b1fc722ddfa154c5517fa6c27822d96717cad092c9e6003'):
        return []
    # Four XYZ vertices become one zero-area point; indices and shading stay.
    blob.write_bytes(bytes(48) + raw[48:])
    return [diagnostic('SELECTION_PLACEHOLDER_COLLAPSED',
        '車体から横へ約11.3m離れた既知の補助四角形をゼロ面積にしました。ノード・台車枠・実車メッシュは保持します。',
        mesh.relative_to(Path(package) / 'content').as_posix(), severity='info')]


def drop_broken_meshes(package) -> list:
    """Delete meshes whose binary data is damaged (ranges outside the file, non-finite positions, ...) and leave the
    nodes that used them out of every model. TPF2 cannot draw such a mesh either; TPF3 may refuse the whole model."""
    content = Path(package) / 'content'
    diagnostics = collapse_selection_placeholder(package)
    diagnostics.extend(repair_known_destination_labels(package))
    broken = {}
    for mesh in sorted(content.rglob('*.msh')):
        try:
            definition = parse(mesh.read_text('utf-8'), str(mesh))
        except (LuaParseError, ValueError, UnicodeError, OSError):
            continue  # not our business here; the package validation reports an unreadable definition
        errors = [entry for entry in validate_mesh(definition, Path(str(mesh) + '.blob')) if entry['severity'] == 'error']
        if errors:
            broken[_normalized(mesh.relative_to(content).as_posix())] = (mesh, errors[0]['code'])
    if not broken:
        return diagnostics
    for model in sorted(content.rglob('*.mdl')):
        try:
            data = parse(model.read_text('utf-8'), str(model))
        except (LuaParseError, ValueError, UnicodeError, OSError):
            continue
        changed = False
        for lod in array(data.get('lods', {})):
            root = lod.get('node') if isinstance(lod, dict) else None
            stack = [root] if isinstance(root, dict) else []
            while stack:
                node = stack.pop()
                children = array(node.get('children', {}))
                kept = [child for child in children if _normalized(child.get('mesh')) not in broken]
                if len(kept) != len(children):
                    node['children'] = table(kept)
                    changed = True
                stack.extend(child for child in kept if isinstance(child, dict))
        if changed:
            prune_missing_axles(data)
            model.write_text(dumps(data), 'utf-8')
    for key, (mesh, code) in sorted(broken.items()):
        for path in (mesh, Path(str(mesh) + '.blob')):
            try:
                path.unlink()
            except OSError:
                pass
        diagnostics.append(diagnostic('MESH_BROKEN_DROPPED', f'メッシュのデータが壊れている（{code}）ため、このメッシュを使う部品を省きました: {key}',
                                      key, severity='warning'))
    return diagnostics
