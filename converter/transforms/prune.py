"""Remove textures from the output package that nothing refers to any more.

Texture conversion writes a converted copy under `_assets/converted_textures/` and points the materials at it, so
the original copy under `_assets/texture/` is dead weight (about half of a typical package). Only files under
`_assets/texture/` are touched, and only inside the output package; the source MOD is never modified.
"""
from pathlib import Path
import re

from converter.adapters.tpf2_reader import diagnostic

_TEXT_SUFFIXES = {'.mtl', '.mdl', '.lua', '.ani', '.mu'}
_TEXTURE_REF = re.compile(r'"([^"\n]+\.(?:dds|tga|png))"', re.I)


def prune_unused_textures(package) -> list:
    content = Path(package) / 'content'
    texture_root = content / '_assets' / 'texture'
    if not texture_root.is_dir():
        return []
    referenced = set()
    for path in content.rglob('*'):
        if not path.is_file() or path.suffix.lower() not in _TEXT_SUFFIXES:
            continue
        text = path.read_text('utf-8', errors='replace')
        for reference in _TEXTURE_REF.findall(text):
            reference = reference.replace('\\', '/')
            if not reference.startswith(('/', '::/')):
                return []  # a relative reference could point anywhere: keep everything
            referenced.add(reference.lstrip('/').lower())
    removed, freed = 0, 0
    for path in sorted(texture_root.rglob('*')):
        if path.is_file() and path.relative_to(content).as_posix().lower() not in referenced:
            freed += path.stat().st_size
            path.unlink()
            removed += 1
    for directory in sorted((p for p in texture_root.rglob('*') if p.is_dir()), reverse=True):
        if not any(directory.iterdir()):
            directory.rmdir()
    if not removed:
        return []
    return [diagnostic('TEXTURES_PRUNED', f'使われなくなった元のテクスチャ{removed}枚（{freed // 2**20} MiB）を出力から除きました。'
                       '変換後のテクスチャは_assets/converted_textures/にあります。', severity='info')]
