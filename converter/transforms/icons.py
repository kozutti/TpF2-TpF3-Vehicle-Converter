"""Vehicle store icons.

TPF2 keeps them under res/textures/ui/models_20 and models_small, named after
the model path with an "@2x" suffix. TPF3 (see the supplied reference vehicle) keeps them in
an `icons` folder next to the model, named after the model:

    <model dir>/icons/<model>_icon20@2x.tga         <- models_20
    <model dir>/icons/<model>_icon20_cblend@2x.tga
    <model dir>/icons/<model>_icon_small@2x.tga     <- models_small
    <model dir>/icons/<model>_icon_small_cblend@2x.tga
    <model dir>/icons/<model>_store.tga             <- no TPF2 counterpart

Nothing references them from a definition file; the game finds them by name.
Colour icons are normalized to uncompressed 32-bit TGA when necessary;
colour-blend masks retain their separate names. The store picture is generated
from the small icon. Only the output copy is changed, never the source MOD.
"""
from __future__ import annotations
from pathlib import Path, PurePosixPath
import re
import shutil
import struct

from PIL import Image

from converter.adapters.tpf2_reader import cancelled, diagnostic
from converter.parsers.lua import parse
from converter.resolver import array, resource_refs

ICON_ROOTS = {'res/textures/ui/models_20/': 'icon20', 'res/textures/ui/models_small/': 'icon_small'}
ICON_NAME = re.compile(r'(?P<stem>.+?)(?P<blend>_cblend)?(?P<scale>@2x)?\.tga$', re.I)
ICON_OUTPUT = re.compile(r'(?:^|/)icons/[a-z0-9_-]+(?:_icon20|_icon_small)(?:_cblend)?@2x\.tga$|(?:^|/)icons/[a-z0-9_-]+_store\.tga$')
STORE_SIZE = (453, 224)
MAX_ICON_PIXELS = 16 * 1024 * 1024


def icon_mapping(files, mapping, diagnostics):
    """Map TPF2 icon files next to the converted models they belong to.

    Icons without a converted model are left out (and reported) rather than
    copied to a path the game never looks at.
    """
    models = {path.casefold(): path for path in files if path.startswith('res/models/model/') and path.lower().endswith('.mdl')}
    by_name = {}
    for key, path in models.items():
        by_name.setdefault(PurePosixPath(key).stem, []).append(path)
    candidates, unmatched, duplicates = {}, [], []
    for path in files:
        folded = path.casefold()
        for root, kind in ICON_ROOTS.items():
            if not folded.startswith(root):
                continue
            match = ICON_NAME.fullmatch(path[len(root):])
            if not match:
                break
            stem, blend = match['stem'], bool(match['blend'])
            # _cblend may be part of the model name itself (a menu
            # model), rather than the mask suffix. Prefer a literal model.
            full_stem = stem + '_cblend' if blend else stem
            literal = models.get(('res/models/model/' + full_stem + '.mdl').casefold())
            exact = literal or models.get(('res/models/model/' + stem + '.mdl').casefold())
            if literal:
                stem, blend = full_stem, False
            # TPF2 icon folders sometimes disagree with the model's folder; an
            # unambiguous model name is still a safe match, but never wins over
            # an icon sitting in the model's own folder.
            same_name = by_name.get(PurePosixPath(stem).stem.casefold(), [])
            if not exact and full_stem != stem:
                literal_names = by_name.get(PurePosixPath(full_stem).stem.casefold(), [])
                if len(literal_names) == 1:
                    same_name, blend = literal_names, False
            model = exact or (same_name[0] if len(same_name) == 1 else None)
            if model not in mapping:
                unmatched.append(path)
                break
            target = PurePosixPath(mapping[model])
            name = f'{target.stem}_{kind}{"_cblend" if blend else ""}@2x.tga'
            destination = (target.parent / 'icons' / name).as_posix()
            priority = (bool(exact), bool(match['scale']))
            previous = candidates.get(destination)
            if previous is None or priority > previous[1]:
                if previous is not None:
                    duplicates.append(previous[0])
                candidates[destination] = (path, priority)
            else:
                duplicates.append(path)
            break
    result = {}
    for target, (path, priority) in candidates.items():
        result[path] = target
        if not priority[0]:
            diagnostics.append(diagnostic('ICON_MATCHED_BY_NAME', f'アイコンのフォルダーがモデルと異なるため名前で対応づけました: {path}', path, severity='info'))
    for path in duplicates:
        diagnostics.append(diagnostic('ICON_DUPLICATE_SKIPPED', f'同じモデルの画像はモデルと同じフォルダーのもの、次に@2xを優先しました: {path}', path, severity='info'))
    for path in unmatched:
        diagnostics.append(diagnostic('ICON_MODEL_NOT_FOUND', f'アイコンに対応するモデルがない、または重複のため取り込みません: {path}', path, severity='warning'))
    return result


def read_tga(path):
    data = Path(path).read_bytes()
    # Some actual Workshop icons (Series 701) use indexed palette colours.
    # Let Pillow expand those, then return the same BGRA layout as truecolour
    # icons. Pillow resolves the source origin, so the expanded image is top-left.
    if (len(data) >= 18 and data[1] == 1 and data[2] in {1, 9}
            and data[16] == 8 and not data[17] & 0xc0):
        width, height = struct.unpack('<HH', data[12:16])
        if not width or not height or width * height > MAX_ICON_PIXELS:
            return None
        try:
            with Image.open(path) as image:
                if data[7] == 32 and image.palette is not None:
                    # Pillow's TGA plugin marks BGRA palettes as RGB; loading
                    # those requires the RGBA palette mode, as for 16-bit maps.
                    image.palette.mode = 'RGBA'
                return width, height, 40, image.convert('RGBA').tobytes('raw', 'BGRA')
        except (OSError, ValueError, Image.DecompressionBombError):
            return None
    if len(data) < 18 or data[1] != 0 or data[2] not in {2, 10} or data[16] not in {24, 32} or data[17] & 0xc0:
        return None
    width, height = struct.unpack('<HH', data[12:16])
    if not width or not height or width * height > MAX_ICON_PIXELS:
        return None
    start = 18 + data[0]
    stride = data[16] // 8
    pixel_count = width * height
    if data[2] == 2:
        pixels = data[start:start + pixel_count * stride]
        if len(pixels) != pixel_count * stride:
            return None
    else:
        pixels = bytearray()
        cursor, count = start, 0
        while count < pixel_count:
            if cursor >= len(data):
                return None
            packet = data[cursor]; cursor += 1
            length = (packet & 127) + 1
            if count + length > pixel_count:
                return None
            size = stride if packet & 128 else length * stride
            block = data[cursor:cursor + size]
            if len(block) != size:
                return None
            pixels.extend(block * length if packet & 128 else block)
            cursor += size; count += length
    descriptor = data[17]
    if stride == 3:
        pixels = b''.join(bytes(pixels[i:i + 3]) + b'\xff' for i in range(0, len(pixels), 3))
        descriptor = (descriptor & 0xf0) | 8
    return width, height, descriptor, bytes(pixels)


def fit(width, height, pixels, size):
    """Letterbox onto a transparent canvas; interpolate with alpha weights."""
    target_w, target_h = size
    scale = min(target_w / width, target_h / height)
    new_w, new_h = max(1, round(width * scale)), max(1, round(height * scale))
    left, top = (target_w - new_w) // 2, (target_h - new_h) // 2
    canvas = bytearray(target_w * target_h * 4)

    def taps(dst, source_len, new_len):
        position = min(max((dst + .5) * source_len / new_len - .5, 0), source_len - 1)
        low = int(position)
        return low, min(low + 1, source_len - 1), position - low

    columns = [taps(x, width, new_w) for x in range(new_w)]
    for y in range(new_h):
        y0, y1, fy = taps(y, height, new_h)
        row = bytearray()
        for x0, x1, fx in columns:
            weights = ((y0, x0, (1 - fy) * (1 - fx)), (y0, x1, (1 - fy) * fx),
                       (y1, x0, fy * (1 - fx)), (y1, x1, fy * fx))
            alpha = blue = green = red = 0.0
            for yy, xx, weight in weights:
                i = (yy * width + xx) * 4
                a = pixels[i + 3] * weight
                blue += pixels[i] * a
                green += pixels[i + 1] * a
                red += pixels[i + 2] * a
                alpha += a
            if alpha > 0:
                row += bytes((round(blue / alpha), round(green / alpha), round(red / alpha), min(255, round(alpha))))
            else:
                row += b'\0\0\0\0'
        offset = ((top + y) * target_w + left) * 4
        canvas[offset:offset + len(row)] = row
    return bytes(canvas)


def write_tga(path, size, descriptor, pixels):
    header = bytes([0, 0, 2]) + bytes(9) + struct.pack('<HH', *size) + bytes([32, descriptor])
    Path(path).write_bytes(header + pixels)


def copy_asset_variant_icons(package, cancel=None):
    """Reuse a train's preview for its same-body asset variant .

    Match the same model name, coarse geometry and albedo, not just a basename.
    Pantograph poses may differ at close range; body previews remain usable.
    """
    content = Path(package) / 'content'
    diagnostics = []

    def local(owner, reference):
        if not isinstance(reference, str) or '::' in reference:
            raise ValueError('External reference')
        path = (content / reference.lstrip('/') if reference.startswith('/') else owner.parent / reference).resolve()
        if not path.is_relative_to(content.resolve()) or not path.is_file():
            raise ValueError('Missing or outside-package resource')
        return path

    def body(owner):
        model = parse(owner.read_text('utf-8-sig'))
        if not isinstance(model.get('metadata', {}).get('transportVehicle'), dict):
            return None
        lods = array(model.get('lods', {}))
        if not lods:
            return None
        result = []
        def visit(node, ancestors=None):
            transforms = (ancestors or []) + [array(node.get('transf', {}))]
            if node.get('mesh'):
                mesh = local(owner, node['mesh'])
                colours = []
                for reference in array(node.get('materials', {})):
                    material_path = local(owner, reference)
                    params = parse(material_path.read_text('utf-8-sig')).get('params', {})
                    albedo = {key: value for key, value in params.items() if key.startswith('map_albedo')}
                    refs = [local(material_path, ref).relative_to(content.resolve()).as_posix()
                            for ref, _ in resource_refs(albedo)]
                    colours.append((refs, params.get('albedo_scale'), params.get('alpha_scale')))
                result.append((mesh, transforms, colours))
            for child in array(node.get('children', {})):
                visit(child, transforms)
        visit(lods[-1].get('node', {}))
        return result or None

    for asset in sorted((content / 'asset').glob('*.mdl')):
        if cancelled(cancel):
            raise InterruptedError('変換を取り消しました。')
        train = content / 'vehicle/train' / asset.name
        if not train.is_file():
            continue
        try:
            signature = body(asset)
            if signature is None or signature != body(train):
                continue
        except (ValueError, OSError, UnicodeError):
            continue
        made = 0
        for suffix in ('_icon20@2x.tga', '_icon20_cblend@2x.tga', '_icon_small@2x.tga',
                       '_icon_small_cblend@2x.tga', '_store.tga'):
            source = train.parent / 'icons' / (train.stem + suffix)
            target = asset.parent / 'icons' / (asset.stem + suffix)
            if source.is_file() and not target.exists():
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, target)
                made += 1
        if made:
            diagnostics.append(diagnostic('ASSET_VARIANT_ICONS_COPIED',
                f'同じ車体・色テクスチャを持つ車両の画像をアセット側にも用意しました: {train.name}',
                asset.relative_to(content).as_posix(), severity='info', icons_copied=made))
    return diagnostics


def make_store_icons(package, progress=None, cancel=None):
    """Create `<model>_store.tga` from each `<model>_icon_small@2x.tga` lacking one."""
    diagnostics, made = copy_asset_variant_icons(package, cancel), 0
    for icon in sorted(Path(package, 'content').rglob('*_icon20@2x.tga')):
        if cancelled(cancel):
            raise InterruptedError('変換を取り消しました。')
        header = icon.read_bytes()[:18]
        if len(header) >= 18 and (header[1:3] != b'\0\x02' or header[16] != 32):
            image = read_tga(icon)
            if image is not None:
                width, height, descriptor, pixels = image
                write_tga(icon, (width, height), descriptor, pixels)
            else:
                diagnostics.append(diagnostic('ICON_FORMAT_UNSUPPORTED',
                    '小アイコンを対応するTGA形式に変換できません。', icon.name, severity='warning'))
    for small in sorted(Path(package, 'content').rglob('*_icon_small@2x.tga')):
        if cancelled(cancel):
            raise InterruptedError('変換を取り消しました。')
        store = small.with_name(small.name[:-len('_icon_small@2x.tga')] + '_store.tga')
        if store.exists():
            continue
        image = read_tga(small)
        if image is None:
            diagnostics.append(diagnostic('ICON_FORMAT_UNSUPPORTED', '対応するTGA形式として読めないためストア画像を作れません。', small.name, severity='warning'))
            continue
        width, height, descriptor, pixels = image
        source_header = small.read_bytes()[:18]
        if source_header[1:3] != b'\0\x02' or source_header[16] != 32:
            write_tga(small, (width, height), descriptor, pixels)
        write_tga(store, STORE_SIZE, descriptor, fit(width, height, pixels, STORE_SIZE))
        made += 1
    if made:
        diagnostics.append(diagnostic('STORE_ICON_GENERATED',
            f'ストア用画像 {made} 枚を小アイコンから生成しました（TPF2に元画像はありません）。ゲーム内の見た目は未検証です。', severity='info'))
    return diagnostics
