"""TPF3 declarations retaining transforms and animation timing."""
from __future__ import annotations
from copy import deepcopy
import hashlib
import json
import posixpath
import re
from converter.resolver import array, table, resolve, rewrite_refs, placeholder_mesh, placeholder_texture, prune_missing_axles
from converter.adapters.tpf2_reader import diagnostic
from converter.tpf3_sounds import tpf3_shared_sound
from converter.adapters.animation import convert_animation, drop_empty_animation, normalize_embedded_animation, playback_duration, repair_mesh_animations

DOOR_EVENTS = {'open_doors_left', 'close_doors_left', 'open_doors_right', 'close_doors_right'}
STANDARD_EVENTS = DOOR_EVENTS | {'forever', 'drive', 'wheels', 'open_all_doors', 'close_all_doors'}
PART_FIELDS = ['backBackwardParts', 'backForwardParts', 'frontBackwardParts', 'frontForwardParts',
               'innerBackwardParts', 'innerForwardParts']
KNOWN_METADATA = {'availability', 'cost', 'description', 'emission', 'maintenance', 'railVehicle',
                  'transportVehicle', 'seatProvider', 'cameraConfig', 'particleSystem', 'labelList', 'cargoSlotProvider'}
RAIL_FIELDS = {'configs', 'engines', 'soundSet', 'topSpeed', 'weight', 'blinkInterval'}
TRANSPORT_FIELDS = {'carrier', 'compartmentsList', 'compartments', 'capacities', 'seats', 'arrivalDelay', 'departureDelay',
                    'comfortFactor', 'priceFactor', 'maintenanceFactor', 'labelList', 'groupFileName', 'loadSpeed', 'multipleUnitOnly', 'reversible'}
PART_EVENTS = {re.sub(r'([a-z])([A-Z])', r'\1_\2', field).lower() + '_' + state
               for field in PART_FIELDS for state in ('on', 'off')}


# TPF2's stock sound set names and the sound sets TPF3 ships for them. TPF3's own loader maps the
# names the same way (see base/content/model_editor editor_base_mod.lua), so a converted vehicle
# gets the real motor, brake and clack sounds without copying any audio file.
GAME_SOUNDSETS = {
    'train_diesel': '::/vehicle/train/shared/sound/train_diesel.snd',
    'train_electric_modern': '::/vehicle/train/shared/sound/train_electric_modern.snd',
    'train_electric_old': '::/vehicle/train/shared/sound/train_electric_old.snd',
    'train_steam_modern': '::/vehicle/train/shared/sound/train_steam_modern.snd',
    'train_steam_old': '::/vehicle/train/shared/sound/train_steam_old.snd',
    'cabcar_modern': '::/vehicle/train/shared/sound/cabcar_modern.snd',
    'waggon_modern': '::/vehicle/waggon/shared/sound/waggon_modern.snd',
    'waggon_old': '::/vehicle/waggon/shared/sound/waggon_old.snd',
    'waggon_freight_modern': '::/vehicle/waggon/shared/sound/waggon_freight_modern.snd',
    'waggon_freight_old': '::/vehicle/waggon/shared/sound/waggon_freight_old.snd',
}


# TPF2 cargo type names and the TPF3 cargo (or cargo class) they correspond to. TPF3 vehicles list cargo files in
# cargoTypeSet.cargoTypesIncluded and/or cargo classes in cargoClassesIncluded (see the supplied waggons).
CARGO_FILES = {'PASSENGERS': ['passengers'], 'COAL': ['coal'], 'CRUDE': ['crude_oil'], 'OIL': ['crude_oil'], 'FUEL': ['fuel'],
               'GRAIN': ['grain'], 'IRON_ORE': ['iron_ore'], 'LOGS': ['logs'], 'PLANKS': ['planks'], 'PLASTIC': ['plastic'],
               'STEEL': ['steel'], 'STONE': ['stone'], 'MACHINES': ['machines'], 'TOOLS': ['tools'], 'PAPER': ['paper'],
               'FISH': ['fish'], 'CONSTRUCTION_MATERIALS': ['bricks', 'cement']}
CARGO_CLASSES = {'GOODS': ['GOODS'], 'COFFEE': ['GOODS'], 'COFFEE_BERRIES': ['BULK'], 'LIVESTOCK': ['GOODS']}
CARGO_FILES.update({'FOOD': ['tinned_food'], 'ALCOHOL': ['beverages'], 'MEAT': ['meat']})
# TPF3's own cargo files; a TPF2 type of the same name (SAND, CEMENT, ...) maps straight to it
TPF3_CARGOS = frozenset({'beverages', 'books', 'bricks', 'cement', 'chemicals', 'clay', 'clothes', 'coal', 'crude_oil', 'dyes',
                         'fabric', 'fertilizer', 'fish', 'fuel', 'furniture', 'glass', 'grain', 'iron_ore', 'logs', 'machines',
                         'meat', 'paper', 'planks', 'plastic', 'rubber', 'sand', 'sawdust', 'sheet_metal', 'steel', 'stone',
                         'tinned_food', 'tires', 'tools', 'vegetables', 'vehicles', 'wool'})


def cargo_type_set(cargo_type, source, diagnostics):
    """TPF3 cargoTypeSet for a TPF2 cargo type name."""
    included, classes = {}, {}
    if cargo_type in CARGO_FILES:
        included = table([f'::/cargos/{name}/{name}.cargo' for name in CARGO_FILES[cargo_type]])
    elif isinstance(cargo_type, str) and cargo_type.lower() in TPF3_CARGOS:
        included = table([f'::/cargos/{cargo_type.lower()}/{cargo_type.lower()}.cargo'])
    elif cargo_type in CARGO_CLASSES:
        classes = table(CARGO_CLASSES[cargo_type])
    else:
        classes = table(['UNIVERSAL'])
        diagnostics.append(diagnostic('CARGO_TYPE_APPROXIMATED',
            f'貨物 {cargo_type!r} に対応するTPF3の貨物がないため、どの貨物でも積める区分（UNIVERSAL）にしました。', source, severity='warning'))
    return {'cargoTypesIncluded': included, 'cargoTypesExcluded': {}, 'cargoClassesIncluded': classes, 'cargoClassesExcluded': {}}


def compartment_list(transport, source, diagnostics):
    """TPF2 compartments as [[load config, ...], ...]; each load config is {'cargoEntries': [...], 'toHide': [...]}.

    TPF2 writes them as `compartmentsList = {{loadConfigs = {{cargoEntries = ...}}}}` or, in older models, as plain
    nested arrays `compartments = {{{{capacity = 82, type = "PASSENGERS"}}}}`.
    """
    result = []
    for compartment in array(transport.pop('compartmentsList', {})):
        loads = [{'cargoEntries': array(load.get('cargoEntries', {})), 'toHide': array(load.get('toHide', {}))}
                 for load in array(compartment.get('loadConfigs', {})) if isinstance(load, dict)]
        result.append(loads)
    capacities = transport.pop('capacities', None)
    if capacities and not result:
        # the oldest form: one {type, capacity} per cargo, carried at the same time
        for capacity in array(capacities):
            if isinstance(capacity, dict):
                result.append([{'cargoEntries': [dict(capacity)], 'toHide': []}])
    old = transport.pop('compartments', None)
    if old and not result:
        for compartment in array(old):
            loads = []
            for load in array(compartment):
                if isinstance(load, dict) and ('cargoEntries' in load or 'toHide' in load):
                    loads.append({'cargoEntries': array(load.get('cargoEntries', {})), 'toHide': array(load.get('toHide', {}))})
                else:
                    loads.append({'cargoEntries': [e for e in array(load) if isinstance(e, dict)], 'toHide': []})
            result.append(loads)
    return result


def nodes(root, parent=None):
    yield root, parent
    for child in array(root.get('children', {})):
        yield from nodes(child, root)

def name_for(name, ordinal=None):
    safe = re.sub(r'[^a-zA-Z0-9_]+', '_', str(name)).strip('_') or 'node'
    return f'{safe}_{ordinal}' if ordinal is not None else safe


def assign_node_names(lods):
    """Name matching instances independently of LOD traversal offsets.

    Mesh names exclude their LOD suffix; transforms and ancestry disambiguate
    repeated names. Exact duplicate instances retain their occurrence order.
    No nodes are removed or reordered, so all zero-based TPF2 IDs remain valid.
    """
    records, variants, repeated = [], {}, set()
    for lod in lods:
        signatures, occurrences, row, counts = {}, {}, [], {}
        for node, parent in nodes(lod.get('node', {})):
            old_name = str(node.get('name', ''))
            mesh = re.sub(r'_lod\d+(?=\.msh$)', '_lod', node.get('mesh', ''), flags=re.I)
            identity = (old_name, tuple(array(node.get('transf', {}))), mesh,
                        signatures.get(id(parent), ''))
            signature = json.dumps(identity, ensure_ascii=True, separators=(',', ':'))
            # Parent geometry may be merged or use an unrelated low-detail
            # mesh filename. Its logical anchor is its name and transform.
            signatures[id(node)] = json.dumps((old_name, identity[1], identity[3]), separators=(',', ':'))
            occurrence = occurrences.get(signature, 0)
            occurrences[signature] = occurrence + 1
            key = (signature, occurrence)
            safe = name_for(old_name)
            counts[safe] = counts.get(safe, 0) + 1
            variants.setdefault(safe, set()).add(key)
            row.append((node, safe, key))
        records.append(row)
        repeated.update(safe for safe, count in counts.items() if count > 1)
    assigned, occupied = {}, set(variants) - repeated
    for safe, identities in sorted(variants.items()):
        for key in sorted(identities):
            if safe not in repeated:
                assigned[(safe, key)] = safe
                occupied.add(safe)
                continue
            candidate = safe + '_' + hashlib.sha256(repr(key).encode()).hexdigest()[:12]
            while candidate in occupied:
                candidate += '_node'
            assigned[(safe, key)] = candidate
            occupied.add(candidate)
    for row in records:
        for node, safe, key in row:
            node['name'] = assigned[(safe, key)]


def convert_particles(system, names, source, diagnostics):
    result = deepcopy(system)
    allowed = {'child', 'color', 'frequency', 'initialAlpha', 'lifeTime', 'position',
               'size01', 'velocity', 'velocityDampingFactor'}
    for emitter in array(result.get('emitters', {})):
        for field in emitter.keys() - allowed:
            diagnostics.append(diagnostic('PARTICLE_FIELD_UNMAPPED', f'particleSystem.{field} は未対応です。', source))
        child = emitter.get('child')
        if isinstance(child, int) and not isinstance(child, bool) and child in names:
            emitter['child'] = names[child]
        else:
            diagnostics.append(diagnostic('PARTICLE_NODE_UNMAPPED', f'particle child {child} が見つかりません。', source))
        # An empty ID keeps this constant emitter outside the default train's
        # named exhaust/brake effects; do not invent an engine trigger.
        emitter['particleId'] = ''
        def curve(values, wrapped=False):
            return {'curve': table([{'time': time, 'value': {'value': value} if wrapped else value,
                                      'easing': 'Linear'} for time, value in values])}
        if 'color' in emitter:
            color = emitter.pop('color')
            emitter['colorOverLifeTime'] = curve([(0, color), (1, deepcopy(color))])
        alpha = emitter.pop('initialAlpha', 1)
        emitter['alphaOverLifeTime'] = curve([(0, alpha), (1, 0)])
        if 'size01' in emitter:
            sizes = array(emitter.pop('size01'))
            if len(sizes) == 2:
                emitter['sizeOverLifeTime'] = curve([(0, sizes[0]), (1, sizes[1])], True)
            else:
                diagnostics.append(diagnostic('PARTICLE_SIZE_INVALID', 'size01は開始・終了の2値が必要です。', source))
        for field in ('frequency', 'lifeTime', 'velocity'):
            if field in emitter:
                emitter[field] = {'value': emitter[field]}
    if array(result.get('emitters', {})):
        diagnostics.append(diagnostic('PARTICLE_CURVE_ADAPTED',
            '旧粒子の開始不透明度とサイズをTPF3曲線へ移植しました。不透明度は寿命末尾で0へ線形補間します。ゲーム内の見た目は未検証です。',
            source, severity='info'))
    return result

def group_engine_types(source, parsed):
    """Engine types of every vehicle in the multiple units grouped under `source`."""
    types = set()
    for path, unit in parsed.items():
        if not is_multiple_unit(path) or not isinstance(unit, dict):
            continue
        if resolve(unit.get('groupFileName'), path, parsed) != source:
            continue
        for ref in multiple_unit_references({**unit, 'groupFileName': None}, path, parsed):
            member = parsed.get(ref, {}).get('metadata', {}).get('railVehicle', {}) if ref else {}
            types |= {engine.get('type', 'DIESEL') for engine in array(member.get('engines', {}))}
    return types


def body_anchor(lods):
    """Name of the car's `body` mesh node when every LOD has one that sits at the origin, else None."""
    identity = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]
    for lod in lods:
        found = False
        for node, parent in nodes(lod.get('node', {})):
            if node.get('name') == 'body' and node.get('mesh'):
                matrix = node.get('transf')
                values = list(matrix.values()) if isinstance(matrix, dict) else list(matrix or identity)
                if len(values) == 16 and all(isinstance(v, (int, float)) and abs(v - i) < 1e-6 for v, i in zip(values, identity)):
                    found = True
                break
        if not found:
            return None
    return 'body' if lods else None


def edit_distance(a, b):
    previous = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        current = [i]
        for j, y in enumerate(b, 1):
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (x != y)))
        previous = current
    return previous[-1]


def repair_misspelled_keys(values, known, scope, source, diagnostics):
    """A key that is not known but is one or two characters away from a known one (`particlehkstem` for `particleSystem`,
    `oundSet` for `soundSet`) is a typo in the MOD, mostly from a find-and-replace; TPF2 ignores it, so the feature is lost.
    The key is read as the one it was meant to be, unless that key is already there."""
    if not isinstance(values, dict):
        return
    for key in [k for k in values if isinstance(k, str) and k not in known and len(k) >= 6]:
        scored = sorted((edit_distance(key.lower(), good.lower()), good) for good in known if len(good) >= 6)
        if not scored or scored[0][0] > 2 or (len(scored) > 1 and scored[1][0] == scored[0][0]):
            continue
        good = scored[0][1]
        if good in values:
            continue
        values[good] = values.pop(key)
        diagnostics.append(diagnostic('KEY_MISSPELLED_REPAIRED',
            f'{scope}の項目名 {key} は {good} の綴りの誤りと判断し、{good} として読みました（元のMODの誤りです。TPF2ではこの項目は効きません）。',
            source, severity='warning'))


def drop_train_name_labels(labels, source, diagnostics):
    """Leave out the train-name labels (`NAME`, the 列番 / vehicle name painted on the car). The converter never shows them: they
    sit a few millimetres in front of the paint and are cut off by it, and the line and destination labels stay."""
    entries = array(labels.get('labels', {})) if isinstance(labels, dict) else []
    kept = [entry for entry in entries if not (isinstance(entry, dict) and entry.get('type') == 'NAME')]
    if len(kept) == len(entries):
        return
    labels['labels'] = table(kept)
    diagnostics.append(diagnostic('LABEL_NAME_DROPPED',
        f'列番（車両名）のラベル{len(entries) - len(kept)}個を出力しません。路線・行先などのラベルは、そのままです。', source, severity='info'))


def attach_labels_to_body(labels, lods, source, diagnostics):
    """TPF2's empty `childId` puts a label on the model root. TPF3 vehicles keep their labels on the body node (stock models use
    `childId = "body"`), and the body node is what rolls with cant, so a label on the root stays behind on a banked curve."""
    entries = array(labels.get('labels', {})) if isinstance(labels, dict) else []
    loose = [entry for entry in entries if isinstance(entry, dict) and not entry.get('childId')]
    if not loose:
        return
    anchor = body_anchor(lods)
    if anchor is None:
        # no origin `body` node: name the root explicitly. Track-cant MODs (their script retargets root groups) move a `childId = "RootNode"`
        # into its roll envelope, while an empty childId stays outside it.
        for entry in loose:
            entry['childId'] = 'RootNode'
        diagnostics.append(diagnostic('LABELS_ATTACHED_TO_ROOT',
            f'行先表示などのラベル{len(loose)}個を、ルートノードの名前（RootNode）を明示して付けました。「body」ノードが無い（または原点にない）車両です。カントMODは、RootNodeに付いたものを傾く枠に移します。', source, severity='info'))
        return
    for entry in loose:
        entry['childId'] = anchor
    diagnostics.append(diagnostic('LABELS_ATTACHED_TO_BODY',
        f'行先表示などのラベル{len(loose)}個を {anchor} ノードに付けました（TPF3の標準車両と同じ付け方で、カントの傾きに追従します）。', source, severity='info'))


def attach_root_particles_to_body(system, lods, source, diagnostics):
    """Keep root emitters at rest but put their frame inside a cant envelope.

    Track-cant MODs retarget group/childId, not particle child. Prefer a
    static origin body directly below the root. Otherwise an identity child
    preserves the original root frame without guessing coordinates/pivots.
    """
    roots = [lod.get('node') for lod in lods]
    if not roots or any(not isinstance(root, dict) for root in roots):
        return
    root_names = {root.get('name') for root in roots}
    loose = [emitter for emitter in array(system.get('emitters', {}))
             if isinstance(emitter, dict) and isinstance(emitter.get('child'), str)
             and emitter['child'] in root_names]
    if not loose:
        return
    identity = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]
    def origin_body(root):
        for node in array(root.get('children', {})):
            values = array(node['transf']) if node.get('transf') else identity
            if (node.get('name') == 'body' and node.get('mesh') and not node.get('animations')
                    and len(values) == 16 and all(isinstance(v, (int, float)) and abs(v-i) < 1e-6
                                                for v, i in zip(values, identity))):
                return True
        return False
    anchor = 'body'
    if not all(origin_body(root) for root in roots):
        occupied = {node.get('name') for root in roots for node, _ in nodes(root)}
        anchor = 'particle_root'
        suffix = 0
        while anchor in occupied:
            suffix += 1
            anchor = f'particle_root_{suffix}'
        for root in roots:
            children = array(root.get('children', {}))
            children.append({'name': anchor, 'transf': table(identity)})
            root['children'] = table(children)
    for emitter in loose:
        emitter['child'] = anchor
    diagnostics.append(diagnostic('PARTICLES_ATTACHED_TO_BODY_FRAME',
        f'ルートに付いた排煙などの発生点{len(loose)}個を {anchor} に付け直しました。位置と排煙設定を保持し、カント時に車体側の枠へ追従します。',
        source, severity='info'))


def is_group_parent(source, parsed):
    """True when another multiple unit or vehicle model names `source` as its group (parent) model."""
    for path, data in (parsed or {}).items():
        if path == source or not isinstance(data, dict):
            continue
        if is_multiple_unit(path):
            group = data.get('groupFileName')
        else:
            group = data.get('metadata', {}).get('transportVehicle', {}).get('groupFileName') if isinstance(data.get('metadata'), dict) else None
        if isinstance(group, str) and resolve(group, path, parsed) == source:
            return True
    return False


def is_menu_only_group_parent(source, parsed):
    """A group header that no multiple unit uses as an actual vehicle."""
    if not is_group_parent(source, parsed):
        return False
    for path, unit in parsed.items():
        if is_multiple_unit(path) and isinstance(unit, dict):
            for ref in multiple_unit_references({**unit, 'groupFileName': None}, path, parsed):
                if ref == source:
                    return False
    return True


def rail_transport_modes(engine_types):
    """(transportModes, engineTransportModes) exactly as TPF3's own loader derives
    them for a rail vehicle. The TransportMode enum has only TRAIN and
    ELECTRIC_TRAIN for rail, so "DIESEL_TRAIN"-style names are invalid. Electric-only
    engines need a catenary lane; any other engine can use both; a vehicle without
    engine has no engine modes."""
    modes = table(['TRAIN', 'ELECTRIC_TRAIN'])
    if set(engine_types) - {'ELECTRIC'}:
        return modes, table(['TRAIN', 'ELECTRIC_TRAIN'])
    if engine_types:
        return modes, table(['ELECTRIC_TRAIN'])
    return modes, table([])


def generated_sound_name(name):
    """Resource name of a sound set script we generate for a TPF2 sound set."""
    return re.sub(r'[^a-z0-9_]+', '_', name.lower()).strip('_') or 'sound'


def fix_lod_ranges(model, source, diagnostics):
    """TPF3 wants consecutive, non-overlapping LOD ranges; TPF2 allowed overlaps (the next LOD starts before the previous ends)."""
    last, adjusted = 0, False
    for lod in array(model.get('lods', {})):
        if not isinstance(lod, dict):
            continue
        start, end = lod.get('visibleFrom', 0), lod.get('visibleTo', 0)
        if not isinstance(start, (int, float)) or not isinstance(end, (int, float)) or start == end == 0:
            continue
        if start < last:
            lod['visibleFrom'] = start = last
            adjusted = True
        if end <= start:
            lod['visibleTo'] = end = start + 1.0
            adjusted = True
        last = end
    if adjusted:
        diagnostics.append(diagnostic('LOD_RANGE_ADJUSTED', 'LODの表示範囲が重なっていたため、次のLODの開始を前のLODの終了に合わせました。', source, severity='warning'))


def horn_reference(horn, source, files, diagnostics):
    """TPF3 name of a horn sound: TPF3's own file for a TPF2 stock horn, else the MOD's file (None = leave it out)."""
    if not isinstance(horn, str) or not horn:
        return None
    shared = tpf3_shared_sound(horn)
    if shared:
        return shared
    if files is not None and resolve(horn, source, files) is None:
        diagnostics.append(diagnostic('SOUND_FILE_MISSING', f'警笛の音声 {horn} が見つからないため、警笛は出力しません。', source, severity='warning'))
        return None
    return horn


ICON_KEYS = ('icon', 'smallIcon', 'icon3d', 'iconSmall', 'iconSmallCblend', 'smallIconCblend')


def drop_missing_icons(model, source, files, diagnostics):
    """A store icon that the MOD does not contain is left out (the purchase list then shows the generated icon or none)."""
    description = model.get('metadata', {}).get('description') if isinstance(model.get('metadata'), dict) else None
    if files is None or not isinstance(description, dict):
        return
    gone = [key for key in ICON_KEYS if isinstance(description.get(key), str) and description[key]
            and not description[key].startswith('::/') and resolve(description[key], source, files) is None]
    for key in gone:
        del description[key]
    if gone:
        diagnostics.append(diagnostic('ICON_FILE_MISSING', f'アイコン {", ".join(gone)} のファイルが元MODにないため、指定を省きました。', source, severity='warning'))


def fix_matrices_and_material_slots(model, source, files, parsed, diagnostics):
    """Two source slips TPF2 tolerates: a transform with one number too many, and a node whose material list is not as
    long as its mesh's sub-mesh list (the last material is repeated / the surplus dropped)."""
    changed_matrices, changed_slots = 0, 0
    for lod in array(model.get('lods', {})):
        root = lod.get('node') if isinstance(lod, dict) else None
        if not isinstance(root, dict):
            continue
        for node, parent in nodes(root):
            matrix = node.get('transf')
            if isinstance(matrix, dict):
                values = array(matrix)
                if len(values) == 17 and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in values):
                    # the usual cause is a stray extra 0 before the final 1
                    values = values[:15] + values[16:] if values[15] == 0 and values[16] == 1 else values[:16]
                    node['transf'] = table(values)
                    changed_matrices += 1
            mesh = node.get('mesh')
            materials = array(node.get('materials', {}))
            if isinstance(mesh, str) and mesh and materials and parsed and files is not None:
                target = resolve(mesh, source, files)
                definition = parsed.get(target) if target else None
                submeshes = len(array(definition.get('subMeshes', {}))) if isinstance(definition, dict) else 0
                if submeshes and len(materials) != submeshes:
                    node['materials'] = table((materials + [materials[-1]] * submeshes)[:submeshes])
                    changed_slots += 1
    if changed_matrices:
        diagnostics.append(diagnostic('MATRIX_REPAIRED', f'要素が17個ある変換行列{changed_matrices}個を、16個に直しました（余分な0を除去）。', source, severity='warning'))
    if changed_slots:
        diagnostics.append(diagnostic('MATERIAL_SLOTS_ADJUSTED', f'メッシュのサブメッシュ数と材質数が合わないノード{changed_slots}個を、サブメッシュ数に合わせました。', source, severity='warning'))


def drop_missing_geometry(model, source, files, diagnostics):
    """Remove mesh nodes whose mesh file exists nowhere, and cargo slot models that do not exist.

    TPF2 skips such nodes; TPF3 would remove the whole model for one invalid resource, so the vehicle would vanish.
    """
    if files is None:
        return
    missing = set()

    def keep(node):
        mesh = node.get('mesh')
        if isinstance(mesh, str) and mesh and resolve(mesh, source, files) is None and not placeholder_mesh(mesh) \
                and not mesh.startswith('::/'):
            missing.add(mesh)
            return False
        return True

    for lod in array(model.get('lods', {})):
        root = lod.get('node') if isinstance(lod, dict) else None
        if not isinstance(root, dict):
            continue
        for node, parent in list(nodes(root)):
            animations = node.get('animations')
            if isinstance(animations, dict):
                for event, animation in list(animations.items()):
                    ref = animation.get('params', {}).get('id') if isinstance(animation, dict) and isinstance(animation.get('params'), dict) else None
                    if isinstance(ref, str) and ref and not ref.startswith('::/') and resolve(ref, source, files) is None:
                        missing.add(ref)
                        del animations[event]
            skin = node.get('skin')
            if isinstance(skin, str) and skin and not skin.startswith('::/') and resolve(skin, source, files) is None:
                missing.add(skin)
                for key in ('skin', 'skinMaterials'):
                    node.pop(key, None)  # the skinned mesh is missing: the node stays as a plain group
            children = array(node.get('children', {}))
            if any(not keep(child) for child in children):
                node['children'] = table([child for child in children if keep(child)])
    provider = model.get('metadata', {}).get('cargoSlotProvider') if isinstance(model.get('metadata'), dict) else None
    if isinstance(provider, dict):
        slots = []
        for slot in array(provider.get('slots', {})):
            models = [name for name in array(slot.get('models', {})) if isinstance(name, str)]
            present = [name for name in models if resolve(name, source, files) is not None or name.startswith('::/')]
            if len(present) != len(models):
                missing.update(name for name in models if name not in present)
            if present:
                slot['models'] = table(present)
                slots.append(slot)
        if len(slots) != len(array(provider.get('slots', {}))):
            provider['slots'] = table(slots)
    if missing:
        prune_missing_axles(model)
        sample = sorted(missing)[0]
        diagnostics.append(diagnostic('GEOMETRY_MISSING_DROPPED',
            f'存在しないメッシュ・モデルが{len(missing)}件あるため、その部品を省きました（例: {sample}）。元MODにファイルがありません。', source, severity='warning'))


def convert_model(original, source, diagnostics, parsed=None, soundsets=None, files=None, mapping=None):
    model = deepcopy(original)
    if model.get('version') not in (None, 0, 1):  # a missing version is the oldest TPF2 form
        diagnostics.append(diagnostic('MODEL_VERSION_UNSUPPORTED', 'version=1のTPF2モデルのみ対応しています。', source))
    metadata = model.get('metadata', {})
    if not metadata.get('railVehicle'):
        if any(kind in metadata for kind in ('roadVehicle', 'waterVehicle', 'airVehicle')):
            diagnostics.append(diagnostic('MODEL_KIND_UNSUPPORTED', '鉄道以外の車両のメタデータ変換は未対応です。', source))
            model['version'] = 2
        else:
            # a plain model (container, cargo, prop) that a rail vehicle uses: name its nodes, order its LODs, mark it version 2
            lods = array(model.get('lods', {}))
            for lod in lods:
                if isinstance(lod, dict):
                    lod.setdefault('node', {})
            assign_node_names([lod for lod in lods if isinstance(lod, dict)])
            lods.sort(key=lambda lod: lod.get('visibleTo', 0) if isinstance(lod.get('visibleTo', 0), (int, float)) else 0)
            if lods:
                model['lods'] = table(lods)
            model['version'] = 2
            drop_missing_geometry(model, source, files, diagnostics)
            fix_matrices_and_material_slots(model, source, files, parsed, diagnostics)
        return model
    repair_misspelled_keys(metadata, KNOWN_METADATA, 'メタデータ', source, diagnostics)
    repair_misspelled_keys(metadata.get('railVehicle'), RAIL_FIELDS, 'railVehicle', source, diagnostics)
    repair_misspelled_keys(metadata.get('transportVehicle'), TRANSPORT_FIELDS, 'transportVehicle', source, diagnostics)
    for field in metadata.keys() - KNOWN_METADATA - {key for key, value in metadata.items() if key == 'seats' and not value}:
        diagnostics.append(diagnostic('METADATA_UNMAPPED', f'メタデータ {field} はTPF3での対応が未確定のため、出力しません。', source, severity='warning'))
    rail = metadata['railVehicle']
    # an empty engine entry (`engines = {{}}`) means an unpowered car; TPF3's loader adds up engine.power and would fail on it
    engines = [engine for engine in array(rail.get('engines', {})) if isinstance(engine, dict) and engine]
    for engine in engines:
        engine.setdefault('power', 0)
    if 'engines' in rail:
        rail['engines'] = table(engines)
    for field in rail.keys() - RAIL_FIELDS:
        diagnostics.append(diagnostic('RAIL_FIELD_UNMAPPED', f'railVehicle.{field} の移植規則がないため、出力しません。', source, severity='warning'))
    for field in metadata.get('transportVehicle', {}).keys() - TRANSPORT_FIELDS:
        diagnostics.append(diagnostic('TRANSPORT_FIELD_UNMAPPED', f'transportVehicle.{field} の移植規則がないため、出力しません。', source, severity='warning'))
    auto_emission = metadata.get('emission', {})
    legacy_emission = {key: auto_emission[key] for key in ('idleEmission', 'speedEmission', 'powerEmission')
                       if isinstance(auto_emission.get(key), (int, float)) and auto_emission[key] != -1}
    unknown_emission = [key for key in auto_emission if key not in {'idleEmission', 'speedEmission', 'powerEmission'}
                        and auto_emission[key] != -1]
    if unknown_emission:
        diagnostics.append(diagnostic('EMISSIONS_UNMAPPED', f'TPF2 emissionの項目 {unknown_emission} のTPF3での対応は未確定です。', source))
    configs = array(rail.get('configs', {}))
    lods = array(model.get('lods', {}))
    for lod in lods:
        if isinstance(lod, dict):
            root_node = lod.setdefault('node', {})  # a LOD without geometry still needs its (empty) root for naming
            if isinstance(root_node, dict) and root_node and all(isinstance(key, int) for key in root_node):
                # `node = {{...}, {...}}`: a list of top-level nodes instead of one root node; put them under a root
                lod['node'] = {'transf': table([1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]), 'children': root_node}
    stray = 0
    for lod in lods:
        if isinstance(lod, dict) and isinstance(lod.get('node'), dict):
            for node, parent in nodes(lod['node']):
                for key in [k for k in node if isinstance(k, int)]:
                    del node[key]  # positional entries next to the named fields; TPF2 only reads `children`
                    stray += 1
    if stray:
        diagnostics.append(diagnostic('NODE_STRAY_ENTRIES_DROPPED', f'ノードに、childrenの外にある不要な要素が{stray}個あったため省きました。', source, severity='warning'))
    assign_node_names(lods)
    name_maps = []
    axle_nodes_by_lod = []
    dynamic_lods = []
    fake_bogies = {}
    for lod_index, lod in enumerate(lods):
        root = lod.get('node', {})
        flat = list(nodes(root))
        numeric_names = {}
        for index, (node, parent) in enumerate(flat):
            numeric_names[index] = node['name']
        name_maps.append(numeric_names)
        config = configs[lod_index] if lod_index < len(configs) else {}
        axle_refs = set(array(config.get('axles', {})))
        axle_nodes = [(node, parent) for node, parent in flat if node.get('mesh') in axle_refs]
        axle_nodes_by_lod.append(axle_nodes)
        missing_axles = axle_refs - {node.get('mesh') for node, parent in flat}
        if missing_axles and not lod.get('static', False) and lod_index < len(configs):
            diagnostics.append(diagnostic('AXLE_REFERENCE_UNRESOLVED', 'このLODに、指定された車輪のメッシュがありません。このLODでは車輪が回転しません（TPF2も無視します）。', source, severity='warning'))
        for part_field in PART_FIELDS:
            event = re.sub(r'([a-z])([A-Z])', r'\1_\2', part_field).lower()
            for node_id in array(config.get(part_field, {})):
                if not isinstance(node_id, int) or node_id < 1 or node_id >= len(flat):
                    diagnostics.append(diagnostic('PART_INDEX_INVALID', f'{part_field}: ノード番号 {node_id} が範囲外のため、この指定は使いません。', source, severity='warning'))
                    continue
                # TPF2 mesh IDs omit root; target transformator consumes named animation events.
                node = flat[node_id][0]
                animations = node.setdefault('animations', {})
                for state in ['on', 'off']:
                    animations[f'{event}_{state}'] = {'type': 'FILE_REF', 'interpolation': 'NEAREST',
                        'params': {'id': f'::/vehicle/shared/ani/{event}_{state}.ani'}}
        for field, values in config.items():
            if field not in {'axles', 'fakeBogies', *PART_FIELDS} and values:
                diagnostics.append(diagnostic('VEHICLE_BEHAVIOR_UNMAPPED', f'{field}（灯火などの動作設定）はTPF3に対応がないため移植しません。', source, severity='warning'))
        converted_bogies = []
        for bogie in array(config.get('fakeBogies', {})):
            mapped = deepcopy(bogie)
            group = bogie.get('group')
            if isinstance(group, int) and not isinstance(group, bool) and group in numeric_names:
                mapped['group'] = numeric_names[group]
            else:
                diagnostics.append(diagnostic('FAKE_BOGIE_UNMAPPED', f'fakeBogie group {group} をノードに対応できません。', source))
            for field in bogie.keys() - {'group', 'offset', 'position', 'upright'}:
                diagnostics.append(diagnostic('FAKE_BOGIE_UNMAPPED', f'fakeBogie.{field} は未対応です。', source))
            converted_bogies.append(mapped)
        fake_bogies[lod_index + 1] = table(converted_bogies)
        for node, parent in flat:
            for event, animation in list(node.get('animations', {}).items()):
                normalize_embedded_animation(animation, source,
                    f'/lods/{lod_index + 1}/node/{node["name"]}/animations/{event}', diagnostics)
                if drop_empty_animation(node['animations'], event, source, diagnostics):
                    continue
                if event not in STANDARD_EVENTS | PART_EVENTS:
                    diagnostics.append(diagnostic('ANIMATION_TRIGGER_UNMAPPED', f'イベント {event} の駆動条件は未確認です。そのまま出力します。', source, severity='warning'))
            if any(field in node for field in ['skin', 'skinWeights', 'bones']):
                diagnostics.append(diagnostic('SKINNING_UNSUPPORTED', 'スキニング（骨による変形）は移植しません。表示が元と異なる可能性があります。', source, severity='warning'))
    from converter.adapters.bogies import prepare_bogies
    axle_names = prepare_bogies(lods, axle_nodes_by_lod, fake_bogies, source, diagnostics)
    dynamic_lods = [i for i, lod in enumerate(lods)
                    if any(n['name'] in axle_names or n.get('animations') for n, _ in nodes(lod['node']))]
    result_meta = {key: deepcopy(value) for key, value in metadata.items() if key in KNOWN_METADATA - {'railVehicle', 'transportVehicle', 'emission'}}
    if metadata.get('particleSystem'):
        result_meta['particleSystem'] = convert_particles(metadata['particleSystem'], name_maps[0], source, diagnostics)
        attach_root_particles_to_body(result_meta['particleSystem'], lods, source, diagnostics)
    if auto_emission and all(value == -1 for value in auto_emission.values()):
        result_meta['emissions'] = {'noise': {'score': -1}, 'pollution': {'score': -1}}
    elif legacy_emission:
        # TPF3 copies the three legacy values into both noise and pollution, then derives the scores.
        fields = {'idle': 'idleEmission', 'speed': 'speedEmission', 'power': 'powerEmission'}
        # an unset value is written as 0: TPF3's loader then derives it from the score, but compares a missing one with 0 and fails
        values = {new: legacy_emission.get(old, 0) for new, old in fields.items()}
        result_meta['emissions'] = {'noise': dict(values), 'pollution': dict(values)}
    legacy_seats = metadata.get('transportVehicle', {}).get('seats') if isinstance(metadata.get('transportVehicle'), dict) else None
    if legacy_seats and 'seatProvider' not in result_meta:
        provider = {'seats': deepcopy(legacy_seats), 'drivingLicense': 'RAIL'}
        for seat in array(provider['seats']):
            if isinstance(seat, dict) and 'animation' not in seat:
                seat['animation'] = 'idle' if seat.get('standing') else 'sitting'
        result_meta['seatProvider'] = provider
    for field in ['seatProvider', 'cameraConfig', 'cargoSlotProvider']:
        def remap(value):
            if isinstance(value, dict):
                for key, item in list(value.items()):
                    if key == 'group' and isinstance(item, int):
                        value[key] = name_maps[0].get(item, 'RootNode')
                        if item not in name_maps[0]:
                            diagnostics.append(diagnostic('GROUP_REFERENCE_UNMAPPED', f'group {item} が見つからないため、車体全体（ルート）に付けます。', source, severity='warning'))
                    else:
                        remap(item)
        remap(result_meta.get(field, {}))
    from converter.adapters.bogies import center_body_groups
    center_body_groups(lods, axle_names, fake_bogies, result_meta, source, diagnostics)
    # TPF3 does not fill in brakeDeceleration for a model that already has a
    # landVehicle block; its own rail vehicles and the supplied sample use 2.5.
    result_meta['landVehicle'] = {'engines': deepcopy(rail.get('engines', {})),
         'topSpeed': rail.get('topSpeed', 0), 'weightEmpty': rail.get('weight', 0) * 1000,
         'weightMaxPayload': 0, 'friction': .02, 'brakeDeceleration': 2.5}
    # TPF2 files may list their LODs in any order; TPF3 needs them ordered by visibleTo, and its own
    # loader (sortLods) reorders the per-LOD fakeBogies the same way. All node-index work above used
    # the original order, which is also what the game's loader does before it sorts.
    order = sorted(range(len(lods)), key=lambda i: lods[i].get('visibleTo', 0)
                   if isinstance(lods[i].get('visibleTo', 0), (int, float)) else 0)
    if order != list(range(len(lods))):
        position = {old: new for new, old in enumerate(order)}
        model['lods'] = table([lods[i] for i in order])
        fake_bogies = {new + 1: fake_bogies[old + 1] for new, old in enumerate(order)}
        dynamic_lods = [position[i] for i in dynamic_lods]
    result_meta['railVehicle'] = {'config': {'axles': table(sorted(axle_names)), 'fakeBogies': fake_bogies}}
    result_meta['transformatorConfig'] = {'transformator': {'name': '::/vehicle/train/shared/default_train.trf'},
                                          'skipFromLod': max(dynamic_lods, default=-1) + 1}
    transport = deepcopy(metadata.get('transportVehicle', {}))
    transport.pop('seats', None)
    legacy_labels = transport.pop('labelList', None)
    if legacy_labels and 'labelList' not in result_meta:
        result_meta['labelList'] = deepcopy(legacy_labels)
    if isinstance(result_meta.get('labelList'), dict):
        drop_train_name_labels(result_meta['labelList'], source, diagnostics)
        attach_labels_to_body(result_meta['labelList'], lods, source, diagnostics)
    compartments = []
    for loads in compartment_list(transport, source, diagnostics):
        converted_loads = []
        for load in loads:
            entries = [e for e in load['cargoEntries'] if isinstance(e, dict)]
            if not entries:
                continue  # a load configuration without cargo (locomotives, power cars)
            if len(entries) > 1:
                diagnostics.append(diagnostic('CARGO_ENTRIES_TRUNCATED',
                    f'1つの積載設定に貨物が{len(entries)}種類あります。TPF3は1種類までのため、最初の1つだけを使います。', source, severity='warning'))
            entry = deepcopy(entries[0])
            entry['cargoTypeSet'] = cargo_type_set(entry.pop('type', 'PASSENGERS'), source, diagnostics)
            bay = entry.get('cargoBay')
            if isinstance(bay, dict) and isinstance(bay.get('childId'), int) and not isinstance(bay.get('childId'), bool):
                bay['childId'] = name_maps[0].get(bay['childId'], 'RootNode')
            # cargoBay / customCargoModels are turned into TPF3's load indicator by the game's own loader, which reads the
            # old plural `cargoEntries`; every other entry keeps the single `cargoEntry`
            visual = isinstance(bay, dict) or 'customCargoModels' in entry
            if not visual:
                entry['loadIndicator'] = ''
            hidden = []
            for node_id in load['toHide']:
                if node_id in name_maps[0]:
                    hidden.append(name_maps[0][node_id])
                else:
                    diagnostics.append(diagnostic('CARGO_NODE_UNMAPPED', f'toHideノード {node_id} が見つかりません。', source, severity='warning'))
            converted_loads.append({('cargoEntries' if visual else 'cargoEntry'): table([entry]) if visual else entry,
                                    'toHide': table(hidden)})
        if converted_loads:
            compartments.append({'loadConfigs': table(converted_loads)})
    group_file = transport.pop('groupFileName', None)
    if group_file:
        # TPF3: groupFileName is relative to this model and names the group (parent) model, which must not name a
        # group itself. A model that is its own group is the parent.
        here = mapping.get(source) if mapping else None
        target = mapping.get(resolve(group_file, source, files)) if mapping and files and isinstance(group_file, str) else None
        if here and target and target == here:
            pass
        elif here and target:
            transport['groupFileName'] = posixpath.relpath(target, posixpath.dirname(here))
            if transport['groupFileName'].startswith('..'):
                # TPF3 resolves the literal ../ as part of the resource name
                # (actual E3/E5 game log). Keep the parent in its original
                # folder and use the same package-root form as mesh references.
                transport['groupFileName'] = '/' + target
        elif here and parsed and is_group_parent(source, parsed):
            diagnostics.append(diagnostic('VEHICLE_GROUP_SELF_PARENT',
                f'このモデルは他の車両のグループ（親）として使われており、自身のグループ指定 {group_file} は存在しないパスのため、親モデルとして扱います。', source, severity='info'))
        else:
            diagnostics.append(diagnostic('VEHICLE_GROUP_UNMAPPED',
                f'車両グループ {group_file} がこのMODに見つからないため、グループ化せず単独の車両として出力します。', source, severity='warning'))
    transport['compartments'] = table(compartments)
    types = {engine.get('type', 'DIESEL') for engine in array(rail.get('engines', {}))}
    if not types and parsed:
        # An engineless group placeholder must match the depot filters of the
        # multiple units listed under it, or the whole group is filtered out.
        types = group_engine_types(source, parsed)
    transport['transportModes'], transport['engineTransportModes'] = rail_transport_modes(types)
    # The old spellings are what the supplied sample still uses; the game copies
    # them to the current names when those are empty. Keep both identical.
    transport['tags'] = deepcopy(transport['transportModes'])
    transport['engineTags'] = deepcopy(transport['engineTransportModes'])
    transport['compartmentsList'] = deepcopy(transport['compartments'])
    transport['filterTags'] = table(['default'])
    if transport.get('multipleUnitOnly') and parsed and is_menu_only_group_parent(source, parsed):
        # TPF2 hides this menu model even if it carries copied geometry/stats.
        # Keep it for group images/children, but never sell it as a real car.
        transport['filterTags'] = {}
        diagnostics.append(diagnostic('MENU_GROUP_NOT_PURCHASABLE',
            '編成・車両の見出し専用モデルを単体購入から隠しました。グループと実車は保持します。',
            source, severity='info'))
    transport.setdefault('comfortFactor', .5)
    transport.setdefault('priceFactor', 1)
    durations = {'open': [], 'close': []}
    for lod in lods:
        for node, parent in nodes(lod['node']):
            for event, animation in node.get('animations', {}).items():
                if event in DOOR_EVENTS | {'open_all_doors', 'close_all_doors'}:
                    ani = resolve(animation.get('params', {}).get('id'), source, parsed or {})
                    timeline = array((parsed or {}).get(ani, {}).get('times', {}))
                    if animation.get('type') in {'KEYFRAME', 'KEYFRAME_MATRIX'}:
                        timeline = [frame.get('time') for frame in array(animation.get('params', {}).get('keyframes', {}))]
                    duration = playback_duration(timeline)
                    if duration is not None:
                        durations[event.split('_')[0]].append(duration)
                    else:
                        diagnostics.append(diagnostic('DOOR_TIMING_UNMAPPED', f'{event} の再生時間を解決できないため、ドアの待ち時間は既定値のままです。', source, severity='warning'))
    transport.setdefault('arrivalDelay', max(durations['open'], default=0))
    transport.setdefault('departureDelay', max(durations['close'], default=0))
    result_meta['transportVehicle'] = transport
    sound = rail.get('soundSet', {})
    if isinstance(sound, str):
        sound = {'name': sound}  # older models name the sound set directly
    if sound.get('name'):
        own = bool(soundsets) and soundsets.get(f'res/config/sound_set/{sound["name"]}.lua', {}).get('provider') == ''
        plain = not (sound.keys() - {'name', 'horn'})
        if sound['name'] in GAME_SOUNDSETS and not own and plain:
            result_meta['soundConfig'] = {'soundSet': {'name': GAME_SOUNDSETS[sound['name']]}}
            horn = horn_reference(sound.get('horn'), source, files, diagnostics)
            if horn:
                result_meta['soundConfig']['effects'] = {'horn': table([horn])}
            diagnostics.append(diagnostic('SOUNDSET_REFERENCE_ADAPTED',
                f'{sound["name"]} をTPF3標準のサウンド {GAME_SOUNDSETS[sound["name"]]} に対応づけました（TPF3本体の読込処理と同じ対応です）。音声ファイルは同梱しません。', source, severity='info'))
        elif (plain and soundsets
              and f'res/config/sound_set/{sound["name"]}.lua' in soundsets):
            # The MOD (or a MOD it depends on) ships its own sound set; write_soundsets
            # converts the script and its audio into the package.
            result_meta['soundConfig'] = {'soundSet': {'name': f'/_assets/generated/sound/{generated_sound_name(sound["name"])}.snd'}}
            horn = horn_reference(sound.get('horn'), source, files, diagnostics)
            if horn:
                result_meta['soundConfig']['effects'] = {'horn': table([horn])}
        elif soundsets is not None and f'res/config/sound_set/{sound["name"]}.lua' not in soundsets:
            # nothing of the original is left: TPF3's own sound set for this kind of vehicle stands in (a vehicle must not be mute)
            from converter.transforms.sounds import stock_soundset
            stock = GAME_SOUNDSETS[stock_soundset({'metadata': {'landVehicle': {'engines': rail.get('engines', {})}}})]
            result_meta['soundConfig'] = {'soundSet': {'name': stock}}
            diagnostics.append(diagnostic('SOUNDSET_NOT_FOUND',
                f'TPF2サウンドセット {sound["name"]} の定義が、元MOD・同じフォルダーの他MOD・TPF2本体のどこにも見つかりません。'
                f'TPF3標準のサウンド {stock} で代用します（元の音ではありません）。定義を持つMODを同じフォルダーに置くか、'
                'TPF2本体を指定して再変換すると、元の音になります。',
                source, severity='warning'))
        else:
            diagnostics.append(diagnostic('SOUNDSET_UNMAPPED', f'TPF2サウンドセット {sound["name"]} はTPF3用soundConfigへの移植が必要です。', source))
    model['metadata'] = result_meta
    model['version'] = 2
    fix_lod_ranges(model, source, diagnostics)
    drop_missing_geometry(model, source, files, diagnostics)
    drop_missing_icons(model, source, files, diagnostics)
    fix_matrices_and_material_slots(model, source, files, parsed, diagnostics)
    return model

SAMPLERS = {'map_albedo': 'albedoTex', 'map_albedo_opacity': 'albedoOpacityTex',
            'map_normal': 'normalTex', 'map_metal_gloss_ao': 'metalGlossAoTex',
            'map_cblend_dirt_rust': 'cblendDirtRustTex', 'map_cblend': 'cblendTex', 'map_emissive': 'emissiveTex',
            # Dirt and rust overlays: TPF3 names the sampler slots like this (see the supplied reference vehicle).
            'map_dirt': 'dirtTex', 'map_dirt_normal': 'dirtNormalTex',
            'map_rust': 'rustTex', 'map_rust_normal': 'rustNormalTex',
            # Actual TPF3 stock materials (buildings/com/mat/add_com_a.mtl).
            'map_op_1': 'opTex1', 'map_op_2': 'opTex2'}
PROPERTIES = {'albedo_scale', 'normal_scale', 'polygon_offset', 'two_sided', 'alpha_scale', 'alpha_test',
              'emissive_scale', 'color_blend', 'dirt_rust', 'light_receiver', 'operation_1', 'operation_2'}

# What stands in for a texture that exists nowhere: TPF3's own placeholder of the same kind. Masks and glow
# textures have no neutral stand-in, so those parameters are left out (the material simply lacks that effect).
MISSING_TEXTURE_PLACEHOLDERS = {
    'map_albedo': 'unknown_albedo_1k.dds', 'map_albedo_opacity': 'unknown_albedo_1k.dds',
    'map_normal': 'default_normal_map.dds', 'map_dirt_normal': 'default_normal_map.dds',
    'map_rust_normal': 'default_normal_map.dds', 'map_metal_gloss_ao': 'default_metal_gloss_ao.dds'}


def convert_material(original, source, diagnostics, files=None):
    material = deepcopy(original)
    converted = {}
    draw_order = None
    for key, value in original.get('params', {}).items():
        if key in SAMPLERS:
            reference = value.get('fileName') if isinstance(value, dict) else None
            if (files is not None and isinstance(reference, str) and reference.lower().endswith(('.dds', '.tga', '.png'))
                    and resolve(reference, source, files) is None and not placeholder_texture(reference)):
                stand_in = MISSING_TEXTURE_PLACEHOLDERS.get(key)
                if stand_in is None:
                    diagnostics.append(diagnostic('TEXTURE_MISSING_DROPPED',
                        f'{key} のテクスチャ {reference} が無いため、この項目を省きました。', source, severity='warning'))
                    continue
                value = {**value, 'fileName': '::/placeholders/mat/tex/' + stand_in}
            converted[key] = {'fragmentSamplers': {SAMPLERS[key]: deepcopy(value)}}
        elif key.startswith('map_'):
            if re.fullmatch(r'map_op_\d+', key) or re.fullmatch(r'operation_\d+', key):
                diagnostics.append(diagnostic('MATERIAL_PARAMETER_DROPPED', f'{key}（旧式の不透明度マップ）はTPF3に対応がないため省きました。', source, severity='warning'))
                continue
            diagnostics.append(diagnostic('MATERIAL_PARAMETER_UNMAPPED', f'テクスチャパラメータ {key} は未対応です。', source))
            converted[key] = value
        elif key == 'fade_out_range':
            converted[key] = {'vertexProperties': {1: deepcopy(value)}}
        elif key == 'order' and isinstance(value, (int, float)) and not isinstance(value, bool):
            draw_order = value  # TPF3 keeps the draw order beside `params` (`order = 0`), never inside it
        elif key in PROPERTIES:
            converted[key] = {'fragmentProperties': {1: deepcopy(value)}}
            if key == 'emissive_scale' and isinstance(value, dict):
                # TPF3 lights an emissive material only between fromTime and toTime (hours); without
                # them lamps and destination signs stay dark. TPF2 had no window: always glow, as
                # TPF3's own lamp materials do (0 to 24).
                converted[key]['fragmentProperties'][1].setdefault('fromTime', 0)
                converted[key]['fragmentProperties'][1].setdefault('toTime', 24)
        else:
            if key == 'props' or re.fullmatch(r'operation_\d+', key):
                diagnostics.append(diagnostic('MATERIAL_PARAMETER_DROPPED', f'{key}（旧式シェーダーの項目）はTPF3に対応がないため省きました。', source, severity='warning'))
                continue
            diagnostics.append(diagnostic('MATERIAL_PARAMETER_UNMAPPED', f'材質パラメータ {key} のshader段階は未確定です。', source))
            converted[key] = value
    converted['light_receiver'] = {'fragmentProperties': {1: {'isLegacyMaterial': True, 'lightMask': 2}}}
    material['params'] = converted
    if draw_order is not None and 'order' not in material:
        material['order'] = draw_order
    return material

def is_multiple_unit(source):
    return source.startswith('res/config/multiple_unit/') and source.endswith('.lua')


def multiple_unit_references(original, source, files):
    """Source paths of the models a TPF2 multiple unit file names."""
    names = [entry.get('name') for entry in array(original.get('vehicles', {})) if isinstance(entry, dict)]
    names.append(original.get('groupFileName'))
    return [resolve(name, source, files) for name in names if isinstance(name, str) and name]


def convert_multiple_unit(original, source, files, mapping, diagnostics):
    """TPF3 .mu.lua: model names are relative to the .mu.lua file itself.

    The wiki defines `vehicles[].name` and `groupFileName` relative to the file
    and forbids `../`, so the planner places the file in the common directory
    of its models. Without `filterTags` TPF3 uses "default" (vanilla depots);
    it is written explicitly.
    """
    unit = deepcopy(original)
    here = posixpath.dirname(mapping[source])
    def relative(name):
        target = mapping.get(resolve(name, source, files)) if isinstance(name, str) and name else None
        if target is None:
            diagnostics.append(diagnostic('MULTIPLE_UNIT_REFERENCE_UNRESOLVED', f'編成の参照 {name} を解決できません。', source))
            return name
        return posixpath.relpath(target, here)
    for index, entry in enumerate(array(unit.get('vehicles', {})), 1):
        if isinstance(entry, dict):
            entry['name'] = relative(entry.get('name'))
    if unit.get('groupFileName'):
        unit['groupFileName'] = relative(unit['groupFileName'])
    unit.setdefault('filterTags', table(['default']))
    return unit


def write_definition(original, source, files, mapping, mod_id, diagnostics, parsed=None, soundsets=None):
    if is_multiple_unit(source):
        return convert_multiple_unit(original, source, files, mapping, diagnostics)
    if source.endswith('.mdl'):
        value = convert_model(original, source, diagnostics, parsed, soundsets, files, mapping)
    elif source.endswith('.mtl'):
        value = convert_material(original, source, diagnostics, files)
    elif source.endswith('.ani'):
        value = convert_animation(original, source, diagnostics)
    elif source.endswith('.msh'):
        value = deepcopy(original)
        repair_mesh_animations(value, source, diagnostics)
    else:
        value = deepcopy(original)
    return rewrite_refs(value, source, files, mapping, mod_id)
