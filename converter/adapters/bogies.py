"""Keep physical wheel registration and static bogie frames consistent with TPF3."""
import math

from converter.adapters.tpf2_reader import diagnostic
from converter.resolver import array, table

IDENTITY = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]
LINEAR = (0, 1, 2, 4, 5, 6, 8, 9, 10)
# Only a single car body. With two or more body groups that each hold their own bogies (M250 Mc250+M251), TPF2 and TPF3
# turn every such group with its own axles; taking the bogies out of the groups makes the bodies rigid, so the cars no
# longer bend through curves (checked in game, 2026-10-10).
SUPPORTED_BOGIE_COUNTS = (2,)


def matrix(node):
    values = array(node['transf']) if node.get('transf') else IDENTITY[:]
    if len(values) != 16 or any(type(v) not in (int, float) or not math.isfinite(v) for v in values):
        return None
    return values


def multiply(a, b):
    return [sum(a[k * 4 + row] * b[col * 4 + k] for k in range(4))
            for col in range(4) for row in range(4)]


def hidden_static(node):
    values = matrix(node)
    return (values is not None and not node.get('animations')
            and not any(k in node for k in ('skin', 'skinWeights', 'bones'))
            and values[15] == 1 and all(values[i] == 0 for i in (3, 7, 11, *LINEAR)))


def rigid(values):
    if values is None or any(abs(values[i]) > 1e-6 for i in (3, 7, 11)) or abs(values[15] - 1) > 1e-6:
        return False
    for a in range(3):
        for b in range(3):
            if abs(sum(values[a * 4 + k] * values[b * 4 + k] for k in range(3)) - (a == b)) > 1e-5:
                return False
    det = (values[0] * (values[5] * values[10] - values[9] * values[6])
           - values[4] * (values[1] * values[10] - values[9] * values[2])
           + values[8] * (values[1] * values[6] - values[5] * values[2]))
    return det > 0


def walk(node, path=()):
    path = path + (node,)
    yield node, path
    for child in array(node.get('children', {})):
        yield from walk(child, path)


def prepare_bogies(lods, axle_nodes, fake_bogies, source, diagnostics):
    """Return named real axles; retain hidden meshes and all name-based attachments.

    Names are assigned and original numeric metadata indices captured before
    this call. Moving a static bogie preserves its model-space matrix, and
    keeps the body/ancestor nodes to which seats, cameras and labels attach.
    """
    instances = {node['name']: [] for pairs in axle_nodes for node, _ in pairs}
    for lod in lods:
        for node, _ in walk(lod['node']):
            if node['name'] in instances:
                instances[node['name']].append(node)
    hidden = {name for name, variants in instances.items() if all(hidden_static(n) for n in variants)}
    if hidden:
        diagnostics.append(diagnostic('INVISIBLE_AXLES_NOT_REGISTERED',
            '全LODでゼロサイズの非表示車輪を車軸登録から外しました。元のノード・メッシュは保持しています。',
            source, severity='info', nodes=sorted(hidden)))
    names = set(instances) - hidden
    for index, (lod, pairs) in enumerate(zip(lods, axle_nodes), 1):
        root = lod['node']
        parents = {id(p): p for n, p in pairs if n['name'] in names and p is not None and p is not root}
        if parents and len(parents) not in SUPPORTED_BOGIE_COUNTS:
            diagnostics.append(diagnostic('BOGIE_LAYOUT_UNSUPPORTED',
                f'実車輪の親ノードが{len(parents)}個あるため、元の構造を保持しました。曲線での動きはゲームで確認してください。',
                source, severity='warning'))
            continue
        if len(parents) not in SUPPORTED_BOGIE_COUNTS or array(fake_bogies.get(index, {})):
            continue
        paths = {id(node): path for node, path in walk(root)}
        moves = []
        for bogie in parents.values():
            path = paths[id(bogie)]
            if len(path) == 2:
                continue  # already a root child, like the stock vehicles
            ancestors = path[1:-1]
            if (bogie.get('animations') or any(k in bogie for k in ('skin', 'skinWeights', 'bones'))
                    or not rigid(matrix(bogie))
                    or any(id(n) in parents or n.get('animations')
                           or set(n) - {'name', 'transf', 'children', 'animations'}
                           or not rigid(matrix(n)) for n in ancestors)):
                moves = []
                break  # animated/skinned/non-rigid or nested physical frames stay intact
            transform = IDENTITY[:]
            for node in path[1:]:
                transform = multiply(transform, matrix(node))
            moves.append((bogie, path[-2], transform))
        for bogie, parent, transform in moves:
            parent['children'] = table([n for n in array(parent['children']) if n is not bogie])
            bogie['transf'] = table(transform)
            root['children'] = table(array(root.get('children', {})) + [bogie])
        if moves:
            diagnostics.append(diagnostic('BOGIES_PROMOTED_TO_ROOT',
                '静的な車体グループ内の台車を標準車両と同じルート階層へ移しました。位置・向きと名前付き参照は保持しています。',
                source, severity='info', lod=index, nodes=[n['name'] for n, _, _ in moves]))
    return names


GROUP_REFERENCE_FIELDS = ('cameraConfig', 'seatProvider', 'cargoSlotProvider')
ORIGIN_TOLERANCE = 0.5


def references_name(value, name):
    if isinstance(value, dict):
        return any(references_name(v, name) for v in value.values())
    return value == name


def shift_group_references(value, shifts):
    """Seats, cameras and cargo slots hold a matrix relative to their group; the group's origin moved by `shifts[group]` (x, y, z)."""
    if isinstance(value, dict):
        group = value.get('group')
        if isinstance(group, str) and group in shifts and value.get('transf'):
            values = array(value['transf'])
            if len(values) == 16:
                for axis, delta in enumerate(shifts[group]):
                    values[12 + axis] -= delta
                value['transf'] = table(values)
        for item in value.values():
            shift_group_references(item, shifts)


def center_body_groups(lods, axle_names, fake_bogies, result_meta, source, diagnostics):
    """Put the origin of each car-body group at the centre of the bogies it holds.

    A model with two or more body groups under the root, each holding its own bogies (M250 Mc250+M251, EH200), has every
    group origin at the model origin although the bodies sit metres away. A track-cant MOD then makes such a vehicle shake
    on a canted curve (checked in game 2026-10-10). TPF3's own Talent puts each group origin at its car. Only the origin
    moves: the children move back by the same amount, so every node keeps its place in the model, and the seats, cameras
    and cargo slots that hang on the group are shifted the same way.
    """
    others = {key: value for key, value in result_meta.items() if key not in GROUP_REFERENCE_FIELDS + ('railVehicle',)}
    shifts_first_lod = {}
    for index, lod in enumerate(lods):
        root = lod['node']
        paths = {id(node): path for node, path in walk(root)}
        groups = []
        for group in array(root.get('children', {})):
            holders = [node for node, _ in walk(group)
                       if any(child.get('name') in axle_names for child in array(node.get('children', {})))]
            if holders and not group.get('mesh'):
                groups.append((group, holders))
        if len(groups) < 2:
            continue
        fake_groups = {fake.get('group') for fake in array(fake_bogies.get(index + 1, {})) if isinstance(fake, dict)}
        moved, frames = {}, {}
        for group, holders in groups:
            name = group.get('name')
            values = matrix(group)
            if (name in fake_groups or references_name(others, name) or group.get('animations')
                    or any(k in group for k in ('skin', 'skinWeights', 'bones')) or values is None
                    or any(abs(values[i] - IDENTITY[i]) > 1e-9 for i in LINEAR)):
                continue
            children = array(group.get('children', {}))
            if any(matrix(child) is None for child in children):
                continue
            if len(holders) == 1 and holders[0] is group:
                # the group is itself a bogie frame (it holds the wheels directly), as in a head car whose two bogie groups are
                # the only groups: TPF3 turns it about its origin, so the origin belongs at the middle of its wheels, on the rail
                # (z = 0), like a stock bogie and like the bogie groups of the intermediate cars of the same MOD
                if any(node.get('animations') for node, _ in walk(group)):
                    continue  # an animation keeps the frame the node had, so a node moved under it would jump
                world = IDENTITY[:]
                for node in paths[id(group)][1:]:
                    world = multiply(world, matrix(node))
                wheels = [multiply(world, matrix(child)) for child in children if child.get('name') in axle_names]
                if not wheels:
                    continue
                target = (sum(w[12] for w in wheels) / len(wheels), sum(w[13] for w in wheels) / len(wheels), 0.0)
                delta = (target[0] - values[12], target[1] - values[13], target[2] - values[14])
                if max(abs(delta[0]), abs(delta[1])) < ORIGIN_TOLERANCE:
                    continue
                frames[name] = delta
            else:
                xs = []
                for holder in holders:
                    transform = IDENTITY[:]
                    for node in paths[id(holder)][1:]:
                        transform = multiply(transform, matrix(node))
                    xs.append(transform[12])
                dx = sum(xs) / len(xs) - values[12]
                if abs(dx) < ORIGIN_TOLERANCE:
                    continue
                delta = (dx, 0.0, 0.0)
                moved[name] = delta
            for axis in range(3):
                values[12 + axis] += delta[axis]
            group['transf'] = table(values)
            for child in children:
                local = matrix(child)
                for axis in range(3):
                    local[12 + axis] -= delta[axis]
                child['transf'] = table(local)
        if moved or frames:
            if index == 0:
                shifts_first_lod = {**moved, **frames}
        if moved:
            diagnostics.append(diagnostic('BODY_GROUP_ORIGINS_CENTERED',
                '複数の車体グループの原点が車体から離れていたため、各車体の中心へ移しました（中のノードの位置は変えていません）。カントMODで揺れるのを避けます。',
                source, severity='info', lod=index, groups={name: round(delta[0], 3) for name, delta in moved.items()}))
        if frames:
            diagnostics.append(diagnostic('BOGIE_FRAME_ORIGINS_CENTERED',
                '台車のグループ（車輪を直接持つグループ）の原点が台車の中心から離れていたため、車輪の中心（レールの高さ）へ移しました（中のノードの位置は変えていません）。'
                '先頭車の台車と車体の向きがずれるのを避けます。',
                source, severity='info', lod=index, groups={name: [round(v, 3) for v in delta] for name, delta in frames.items()}))
    if shifts_first_lod:
        for field in GROUP_REFERENCE_FIELDS:
            shift_group_references(result_meta.get(field, {}), shifts_first_lod)
