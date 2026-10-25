"""Resource identity and reference resolution; no input Lua is executed."""
from __future__ import annotations
import posixpath
import re
import hashlib
from pathlib import PurePosixPath

# Placeholder textures TPF2 materials fall back on; TPF3 ships the same files (placeholders/mat/tex).
PLACEHOLDER_TEXTURES = frozenset({
    'default_ao.dds', 'default_map_id.dds', 'default_metal_gloss_ao.dds', 'default_normal_map.dds', 'missing_cube.dds',
    'unknown_albedo_1k.dds', 'unknown_albedo_2k.dds', 'unknown_albedo_4k.dds', 'unknown_metal_gloss_ao.dds',
    'unknown_metal_gloss_ao_128.dds', 'unknown_normal.dds', 'unknown_normal_1k.dds', 'unknown_normal_2k.dds',
    'unknown_normal_4k.dds', 'unknown_roughness_1k.dds', 'unknown_roughness_2k.dds', 'unknown_roughness_4k.dds',
    'unknown_texture.dds', 'unknown_transparent_128.dds'})


PLACEHOLDER_MESHES = frozenset({'cube_1x1_lod0.msh'})


def placeholder_mesh(reference):
    """TPF3 resource name of a TPF2 stock placeholder mesh (the 1 m cube group models use), or None."""
    if isinstance(reference, str):
        name = posixpath.basename(reference.replace('\\', '/')).lower()
        if name in PLACEHOLDER_MESHES:
            return '::/placeholders/msh/' + name
    return None


def placeholder_texture(reference):
    """TPF3 resource name for a TPF2 placeholder texture reference, or None."""
    if isinstance(reference, str):
        name = posixpath.basename(reference.replace('\\', '/')).lower()
        if name in PLACEHOLDER_TEXTURES:
            return '::/placeholders/mat/tex/' + name
    return None


MISSING_MATERIAL = '::/placeholders/mat/missing_object.mtl'

# TPF3 ships the people / crew models the TPF2 base game had, one folder per model (names only, no assets)
TPF3_CHARACTERS = frozenset(['era_a_driver_rail', 'era_a_driver_road', 'era_a_driver_water', 'era_a_man_01', 'era_a_man_02', 'era_a_man_03', 'era_a_man_04', 'era_a_wom_01', 'era_a_wom_02', 'era_a_wom_03', 'era_a_wom_04', 'era_b_driver_air_indoor', 'era_b_driver_air_outdoor', 'era_b_driver_rail', 'era_b_driver_road', 'era_b_driver_water', 'era_b_man_01', 'era_b_man_02', 'era_b_man_03', 'era_b_man_04', 'era_b_wom_01', 'era_b_wom_02', 'era_b_wom_03', 'era_b_wom_04', 'era_c_driver_air', 'era_c_driver_helicopter', 'era_c_driver_rail', 'era_c_driver_road', 'era_c_driver_water', 'era_c_man_01', 'era_c_man_02', 'era_c_man_03', 'era_c_man_04', 'era_c_wom_01', 'era_c_wom_02', 'era_c_wom_03', 'era_c_wom_04'])


def tpf3_character(reference):
    """'::/characters/<n>/<n>.mdl' for a TPF2 stock character reference such as 'characters/<n>.mdl', else None."""
    if isinstance(reference, str):
        name = posixpath.basename(reference.replace('\\', '/'))
        stem = name[:-4] if name.lower().endswith('.mdl') else ''
        if stem in TPF3_CHARACTERS and reference.replace('\\', '/').lstrip('/').lower().startswith(('characters/', 'res/models/model/characters/')):
            return f'::/characters/{stem}/{stem}.mdl'
    return None

PREFIXES = {
    '.mdl': 'res/models/model/', '.msh': 'res/models/mesh/',
    '.mtl': 'res/models/material/', '.ani': 'res/models/animation/',
    '.dds': 'res/textures/', '.tga': 'res/textures/', '.png': 'res/textures/',
    '.wav': 'res/audio/effects/',
}


def literal_digest(text):
    """Short SHA-256 of a literal. Corrections for known malformed MODs are keyed by digest, never by name."""
    return hashlib.sha256(text.encode('utf-8')).hexdigest()[:16]


# Digests of the case-folded owner materials, of the case-folded reference, and of the resource the reference should reach.
_MISSING_FOLDER_OWNERS = frozenset({'0b19606331ae2c5b', '3d6fd2bf8a9ed642', '93d5f58b413b15f7', 'c188ecc3b674bd22',
                                    'ffc9655e4b064866'})
_MISSING_FOLDER_REFS = frozenset({'f4a60ce27dc49572'})
_MISSING_FOLDER_TARGETS = frozenset({'8bd83b0bdfaac474'})
RESERVED = {'con', 'prn', 'aux', 'nul', *[f'com{i}' for i in range(1, 10)], *[f'lpt{i}' for i in range(1, 10)]}

def array(value):
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        return [value[k] for k in sorted(k for k in value if isinstance(k, int))]
    return []

def prune_missing_axles(model):
    """Forget wheel nodes that no LOD has any more (their meshes were missing or damaged). True when something changed."""
    metadata = model.get('metadata') if isinstance(model, dict) else None
    config = metadata.get('railVehicle', {}).get('config') if isinstance(metadata, dict) and isinstance(metadata.get('railVehicle'), dict) else None
    if not isinstance(config, dict) or not config.get('axles'):
        return False
    names = {value for key, value, at in walk(model.get('lods', {})) if key == 'name' and isinstance(value, str)}
    axles = array(config['axles'])
    kept = [axle for axle in axles if axle in names]
    if len(kept) == len(axles):
        return False
    config['axles'] = table(kept)
    return True


def table(values):
    return {i: value for i, value in enumerate(values, 1)}

def walk(value, location=''):
    if isinstance(value, dict):
        for key, item in value.items():
            at = f'{location}/{key}'
            yield key, item, at
            yield from walk(item, at)

def output_path(path, mod_id):
    mappings = [('res/models/model/', ''), ('res/models/mesh/', '_assets/mesh/'),
                ('res/models/material/', '_assets/material/'), ('res/models/animation/', '_assets/animation/'),
                ('res/textures/', '_assets/texture/'), ('res/audio/', '_assets/audio/')]
    for prefix, target in mappings:
        if path.startswith(prefix):
            return target + path[len(prefix):]
    if path.startswith('res/config/multiple_unit/'):
        return f'vehicle/train/{mod_id}/' + PurePosixPath(path).stem + '.mu.lua'
    return '_assets/other/' + path

def portable_path(path):
    parts = []
    for part in path.split('/'):
        safe = re.sub(r'[^a-z0-9_.-]+', '_', part.lower())
        if safe != part.lower() or not safe.strip('_'):
            # The hash goes between the name and its extension(s): TPF3 recognizes a resource by its
            # extension, and "x.msh" and "x.msh.blob" must stay a matching pair, so the hash is taken from
            # the part before the first dot.
            stem, dot, extension = safe.partition('.')
            digest = hashlib.sha256(part.partition('.')[0].encode()).hexdigest()[:8]
            safe = (stem.strip('_') or 'asset') + '_' + digest + dot + extension
        if safe.split('.')[0] in RESERVED:
            safe = 'asset_' + safe
        parts.append(safe)
    return '/'.join(parts)

def resolve(reference, owner, files):
    return resolve_indexed(reference, owner, {f.casefold(): f for f in files})


def resolve_indexed(reference, owner, lookup):
    """resolve() against a prebuilt {casefolded path: path} mapping (no per-call index build)."""
    if not isinstance(reference, str) or not reference:
        return None
    ref = re.sub(r'/{2,}', '/', reference.replace('\\', '/'))
    if ref.startswith('::/') or ':/' in ref:
        return ref
    if ref.startswith('/') or '..' in PurePosixPath(ref).parts:
        return None
    # TPF2 accepts repeated separators. Keep the raw traversal check above,
    # then normalize before adding the resource namespace prefix.
    ref = posixpath.normpath(ref)
    suffix = PurePosixPath(ref).suffix.lower()
    candidates = [ref, posixpath.normpath(posixpath.join(posixpath.dirname(owner), ref))]
    if suffix in PREFIXES:
        candidates.insert(0, PREFIXES[suffix] + ref)
    target = next((lookup[p.casefold()] for p in candidates if p.casefold() in lookup), None)
    # A few known materials omit a texture subfolder. Recover only this proven reference, only for those materials,
    # and only when no correctly located original resource exists.
    if target is None and literal_digest(owner.casefold()) in _MISSING_FOLDER_OWNERS \
            and literal_digest(ref.casefold()) in _MISSING_FOLDER_REFS:
        target = next((value for path, value in lookup.items() if literal_digest(path) in _MISSING_FOLDER_TARGETS), None)
    return target


def resource_refs(data):
    for key, item, at in walk(data):
        # Node identifiers can end in .msh; the mesh field is the file reference.
        parent = at.rsplit('/', 1)[0]
        if key == 'name' and at.startswith('/lods/') and re.search(r'/(?:node|children/\d+)$', parent):
            continue
        if isinstance(item, str) and PurePosixPath(item).suffix.lower() in PREFIXES:
            yield item, at

def rewrite_refs(data, owner, files, mapping, mod_id):
    if isinstance(data, dict):
        return {k: rewrite_refs(v, owner, files, mapping, mod_id) for k, v in data.items()}
    if isinstance(data, str):
        target = resolve(data, owner, files)
        if target is None and placeholder_texture(data):
            return placeholder_texture(data)
        if target is None and tpf3_character(data):
            return tpf3_character(data)
        if target is None and placeholder_mesh(data):
            return placeholder_mesh(data)
        if target is None and data.lower().endswith('.mtl') and not data.startswith('::/'):
            return MISSING_MATERIAL  # a material that exists nowhere: TPF3's own placeholder keeps the model loadable
        if target in mapping:
            # Explicit mod identifier avoids resource-relative parent traversal.
            return '/' + mapping[target]
    return data
