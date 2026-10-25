"""TPF2 preview pictures -> TPF3 Mod Browser _metadata/<number>.png."""
from pathlib import Path
import re

from PIL import Image, ImageOps

from converter.adapters.tpf2_reader import cancelled, diagnostic, notify

PREVIEW_SIZE = (1920, 1080)
PREVIEW_NAME = re.compile(r'image_(\d+)\.(tga|png|jpe?g)$', re.I)


def write_mod_previews(source, package, progress=None, cancel=None):
    source, package = Path(source), Path(package)
    candidates = []
    for path in source.iterdir():
        match = PREVIEW_NAME.fullmatch(path.name)
        if path.is_file() and match:
            candidates.append((int(match[1]), path.name.casefold(), path))
    candidates.sort(key=lambda row: row[:2])
    paths = [row[2] for row in candidates]
    if not paths:
        paths = [source / name for name in ('workshop_preview.png', 'workshop_preview.jpg', 'preview.png', 'preview.jpg')
                 if (source / name).is_file()]
    # A vehicle's existing purchase picture also makes a useful preview for
    # MODs whose authors did not supply a Workshop picture.
    if not paths:
        paths = sorted((package / 'content').rglob('*_store.tga'))[:1]
    metadata = package / '_metadata'
    diagnostics, written = [], 0
    for index, path in enumerate(paths):
        if cancelled(cancel):
            raise InterruptedError('変換を取り消しました。')
        notify(progress, 'mod_preview', index + 1, len(paths), path.name)
        try:
            with Image.open(path) as image:
                if image.width * image.height > 64 * 1024 * 1024:
                    raise ValueError('プレビュー画像が64メガピクセルを超えています。')
                picture = ImageOps.exif_transpose(image).convert('RGBA')
                picture = ImageOps.contain(picture, PREVIEW_SIZE, Image.Resampling.LANCZOS)
                canvas = Image.new('RGB', PREVIEW_SIZE, (30, 35, 40))
                position = ((PREVIEW_SIZE[0] - picture.width) // 2, (PREVIEW_SIZE[1] - picture.height) // 2)
                canvas.paste(picture, position, picture)
                metadata.mkdir(parents=True, exist_ok=True)
                canvas.save(metadata / f'{written}.png', format='PNG')
            diagnostics.append(diagnostic('MOD_PREVIEW_CONVERTED',
                f'MOD一覧画像を作成しました: {path.name} → _metadata/{written}.png', path.name, severity='info'))
            written += 1
        except (OSError, ValueError, Image.DecompressionBombError) as exc:
            diagnostics.append(diagnostic('MOD_PREVIEW_INVALID',
                f'MODプレビュー画像を読み込めません: {path.name}: {exc}', path.name, severity='warning'))
    if not written:
        diagnostics.append(diagnostic('MOD_PREVIEW_MISSING',
            '元MODに使用可能なプレビュー画像がないため、MOD一覧画像を作成できませんでした。', severity='warning'))
    return diagnostics
