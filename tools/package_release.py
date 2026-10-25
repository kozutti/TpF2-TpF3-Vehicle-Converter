"""Collect installed dependency notices and an explicit, sample-free source archive."""
from pathlib import Path
import importlib.metadata
import shutil
import sys
import zipfile

root = Path(__file__).resolve().parents[1]
release = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else root / 'release'
portable = release / 'TPF3VehicleConverter'
notices = portable / 'licenses'
notices.mkdir(parents=True, exist_ok=True)
for name in ['tkinterdnd2', 'wasmtime', 'Pillow', 'lupa', 'pyinstaller', 'pyinstaller-hooks-contrib', 'altgraph', 'pefile', 'pywin32-ctypes']:
    distribution = importlib.metadata.distribution(name)
    for relative in distribution.files or []:
        if any(token in relative.name.lower() for token in ('license', 'copying', 'copyright')):
            source = Path(distribution.locate_file(relative))
            if source.is_file():
                target = notices / name / str(relative).replace('\\', '/')
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, target)
for source in Path(sys.base_prefix).glob('LICENSE*'):
    if source.is_file():
        shutil.copyfile(source, notices / ('Python-' + source.name))
for source in (root / 'LICENSES').glob('*'):
    if source.is_file():
        shutil.copyfile(source, notices / source.name)
for directory in [portable / '_internal' / '_tcl_data', portable / '_internal' / '_tk_data']:
    for source in directory.rglob('*'):
        if source.is_file() and source.name.lower() in {'license', 'license.terms', 'license.txt'}:
            shutil.copyfile(source, notices / (directory.name + '-' + source.name))
source_zip = release / 'TPF3VehicleConverter-Source.zip'
entries = []
for directory in ['converter', 'tests', 'docs', 'tools', 'LICENSES']:
    entries.extend(p for p in (root / directory).rglob('*')
                   if p.is_file() and not {'__pycache__', 'target'} & set(p.parts) and p.suffix != '.pyc')
entries.extend(root / name for name in ['launcher.py', 'README.md', 'CHANGELOG.md', 'GAME_CHECKLIST.md', 'LICENSE',
                                      'requirements.txt', 'requirements-build.txt', 'build_portable.ps1']
               if (root / name).is_file())
with zipfile.ZipFile(source_zip, 'w', zipfile.ZIP_DEFLATED) as archive:
    for path in sorted(entries):
        archive.write(path, 'TPF3VehicleConverter-Source/' + path.relative_to(root).as_posix())
print(source_zip)
