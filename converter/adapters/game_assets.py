"""Read-only, dependency-only access to configured TPF2 resources."""
from contextlib import contextmanager
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import tempfile
import zipfile

from converter.parsers.lua import _Parser, parse, LuaParseError
from converter.resolver import PREFIXES, resolve_indexed
from converter.adapters.tpf2_reader import cancelled, notify, sha256
from converter.adapters.source_repairs import repair_model_text
from converter.parsers.lua_sandbox import _BUILTIN

_REQUIRE = re.compile(r'''\brequire\s*\(?\s*["']([^"']+)["']''')

def module_scripts(text):
    """`require "a.b"` -> `res/scripts/a/b.lua` for the modules a MOD's own libraries ship (not the sandbox's built-ins)."""
    return ['res/scripts/'+name.replace('.','/')+'.lua' for name in dict.fromkeys(_REQUIRE.findall(text))
            if name not in _BUILTIN and '..' not in name and not any(c in name for c in (':', '/', chr(92)))]

def literals(text, name):
    return [t.value for t in _Parser(text,name,lex_only=True).tokens if t.kind=='string']

def safe_relative(value):
    p=PurePosixPath(value.replace('\\','/'))
    if p.is_absolute() or '..' in p.parts or ':' in value:
        raise ValueError(f'不正なゲーム資産パス: {value}')
    return p.as_posix()

def walk_files(root):
    for directory,dirs,names in os.walk(root,followlinks=False):
        dirs[:]=[d for d in dirs if not (Path(directory)/d).is_symlink() and not (Path(directory)/d).is_junction()]
        for name in names:
            path=Path(directory)/name
            if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
                raise ValueError(f'資産フォルダー外を指すリンク: {path}')
            yield path

class GameResources:
    def __init__(self,folder,cancel=None):
        folder=Path(folder).resolve()
        self.root=folder.parent if folder.name.lower()=='res' else folder
        res=self.root/'res'
        if not res.is_dir(): raise ValueError('TPF2本体のresフォルダーが見つかりません。')
        self.index={};self.names={};archives=[]
        for path in walk_files(res):
            if cancelled(cancel): raise InterruptedError('ゲーム資産検索を取り消しました。')
            rel=path.relative_to(self.root).as_posix()
            if path.suffix.lower()=='.zip': archives.append(path);continue
            self.index[rel]=(path,None);self.names[rel.casefold()]=rel
            if len(self.index)>250000: raise ValueError('ゲーム資産の一覧は250,000件までです。')
        for archive in sorted(archives):
            with zipfile.ZipFile(archive) as z:
                for entry in z.infolist():
                    if entry.is_dir(): continue
                    member=safe_relative(entry.filename)
                    rel=member if member.startswith('res/') else archive.parent.relative_to(self.root).as_posix()+'/'+member
                    if rel.casefold() not in self.names:
                        self.index[rel]=(archive,entry.filename);self.names[rel.casefold()]=rel
                    if len(self.index)>250000: raise ValueError('ゲーム資産の一覧は250,000件までです。')

    def find(self,reference,owner):
        return resolve_indexed(reference,owner,self.names)

    def read(self,rel):
        path,member=self.index[rel]
        if member is None:
            if path.stat().st_size>512*1024**2: raise ValueError('ゲーム資産単体は512MiBまでです。')
            return path.read_bytes()
        with zipfile.ZipFile(path) as archive:
            entry=archive.getinfo(member)
            if entry.file_size>512*1024**2 or entry.flag_bits&1:
                raise ValueError('ゲームZIP資産が大きすぎるか暗号化されています。')
            return archive.read(entry)


class SiblingIndex:
    """Lazy file index of the other TPF2 MODs that sit next to the MOD being converted.

    TPF2 merges the res folders of all active MODs, so a vehicle MOD may point at
    meshes, materials or textures that only another MOD (for example a base
    pack the add-on was made for) ships. Only the file names are indexed here;
    a provider's files are read later and only when something references them.
    """
    LIMIT=3_000_000

    def __init__(self,folder,cancel=None):
        self.folder=Path(folder).resolve();self.cancel=cancel
        self.names={};self.owners={};self._built=False;self.mods=[]

    def candidate_mods(self):
        """Direct MODs, then MODs inside one grouping folder; never follow links."""
        try:
            children = sorted(p for p in self.folder.iterdir()
                              if p.is_dir() and not p.is_symlink() and not p.is_junction())
        except OSError:
            return []
        direct = [p for p in children if (p / 'res').is_dir()]
        nested = []
        for group in children:
            if group in direct or (group / 'mod.lua').is_file():
                continue
            try:
                nested.extend(p for p in sorted(group.iterdir()) if p.is_dir() and not p.is_symlink()
                              and not p.is_junction() and (p / 'res').is_dir())
            except OSError:
                continue
        return direct + nested

    def _build(self):
        if self._built: return
        self._built=True
        children=self.candidate_mods()
        self.mods=children
        for child in children:
            if cancelled(self.cancel): raise InterruptedError('他MODの資産検索を取り消しました。')
            res=child/'res'
            for directory,dirs,files in os.walk(res,followlinks=False):
                dirs[:]=[d for d in dirs if not (Path(directory)/d).is_symlink() and not (Path(directory)/d).is_junction()]
                for name in files:
                    if name.lower().endswith('.zip'): continue
                    rel=(Path(directory)/name).relative_to(child).as_posix()
                    key=rel.casefold()
                    if key not in self.names:
                        self.names[key]=rel;self.owners[rel]=[]
                    self.owners[self.names[key]].append(child)
                if len(self.names)>self.LIMIT: raise ValueError('他MODの資産一覧が上限を超えました。')

    def locate(self,reference,owner,exclude=None):
        """Folder of the first other MOD that has the referenced file, or None."""
        self._build()
        rel=resolve_indexed(reference,owner,self.names)
        if rel is None or rel not in self.owners: return None
        skip=Path(exclude).resolve() if exclude is not None else None
        return next((m for m in self.owners[rel] if skip is None or m!=skip),None)

    def has_other_mods(self,exclude=None):
        """Cheap check used before offering the search: are there other MOD folders here?"""
        try:
            skip=Path(exclude).resolve() if exclude is not None else None
            return any(p.resolve()!=skip for p in self.candidate_mods())
        except OSError: return False

def local_soundsets(root):
    """The MOD's own TPF2 sound set scripts, including nested resource paths."""
    found = {}
    folder = Path(root) / 'res' / 'config' / 'sound_set'
    if folder.is_dir() and not folder.is_symlink():
        for path in sorted(walk_files(folder)):
            if path.suffix.lower() != '.lua':
                continue
            if path.is_symlink() or not path.is_file() or path.stat().st_size > 16 * 1024**2:
                continue
            found[path.relative_to(root).as_posix()] = {'text': path.read_text('utf-8-sig'), 'source': str(path), 'provider': ''}
    return found


SOUND_SET_DIR = 'res/config/sound_set/'
_SOUND_NAME = re.compile(r'''soundSet\s*=\s*(?:\{[^{}]*?\bname\s*=\s*["']([^"']+)["']|["']([^"']+)["'])''')


def referenced_soundsets(local):
    """Casefolded `res/config/sound_set/<name>.lua` paths that the MOD's own vehicle models name."""
    found = set()
    for rel, path in local.items():
        if rel.startswith('res/models/model/') and rel.endswith('.mdl') and path.stat().st_size <= 16 * 1024**2:
            for table_name, plain_name in _SOUND_NAME.findall(path.read_text('utf-8-sig', errors='replace')):
                name = (table_name or plain_name).strip().removesuffix('.lua')
                if name:
                    found.add((SOUND_SET_DIR + name + '.lua').casefold())
    return found


def sound_overrides(local, siblings, exclude=None):
    """Sound-only add-on MODs that replace sound sets the MOD ships and its models use.

    TPF2 merges the res folders of all active MODs, so a MOD without rail vehicles that ships the same
    `res/config/sound_set/<name>.lua` replaces it (a "sound mod" for a repaint pack). Returns
    ({the MOD's own sound set path: add-on folder}, [(path, [add-on folders])] where several add-ons compete).
    """
    from converter.batch import classify_mod  # lazy: converter.batch imports this module
    wanted = referenced_soundsets(local)
    names = [rel for rel in local if rel.casefold() in wanted and rel.casefold().startswith(SOUND_SET_DIR) and rel.endswith('.lua')]
    if not names:
        return {}, []
    siblings._build()
    skip = Path(exclude).resolve() if exclude is not None else None
    sound_only, found, conflicts = {}, {}, []
    for rel in sorted(names):
        sibling_rel = siblings.names.get(rel.casefold())
        candidates = []
        for folder in siblings.owners.get(sibling_rel, []) if sibling_rel else []:
            folder = Path(folder).resolve()
            if folder == skip:
                continue
            if folder not in sound_only:
                sound_only[folder] = classify_mod(folder)['rail_models'] == 0
            if sound_only[folder]:
                candidates.append(folder)
        if len(candidates) == 1:
            found[rel] = candidates[0]
        elif candidates:
            conflicts.append((rel, candidates))
    return found, conflicts


@contextmanager
def game_input(root,game_root=None,progress=None,cancel=None,siblings=None,exclude=None):
    """Yield `root`, or an overlay of it with the missing referenced assets added.

    Missing assets are looked up in the configured TPF2 installation(s) (`game_root`
    is one folder or a list) and then, if given, in the other MODs of `siblings`
    (a SiblingIndex; `exclude` is the MOD being converted). The MOD's own files
    always win. Nothing outside the overlay is modified.
    """
    root=Path(root)
    roots=[] if game_root is None else ([game_root] if isinstance(game_root,(str,Path)) else list(game_root))
    # The current TPF2 base replaced some models with *_v2. Its official
    # legacy pack still supplies the exact old meshes/animations repaints use.
    # Search it after every explicitly configured root, without changing it.
    configured = list(roots)
    seen_roots = {str(Path(r).resolve()).casefold() for r in roots}
    for folder in configured:
        folder = Path(folder).resolve()
        base = folder.parent if folder.name.casefold() == 'res' else folder
        legacy = base / 'mods/urbangames_legacy_vehicle_pack_1'
        if (legacy / 'res').is_dir() and str(legacy).casefold() not in seen_roots:
            roots.append(legacy)
            seen_roots.add(str(legacy).casefold())
    info={'game_root':None,'reference_roots':[],'game_dependencies':{},'game_soundsets':local_soundsets(root),'imported_from':{},'sound_overrides':{},'sound_override_conflicts':[]}
    if not roots and siblings is None:
        yield root,info;return
    games=[GameResources(r,cancel) for r in roots]
    if games: info['game_root']=str(games[0].root);info['reference_roots']=[str(g.root) for g in games]
    pool={g.root:g for g in games}
    local={p.relative_to(root).as_posix():p for p in walk_files(root)}
    local_lookup={k.casefold():k for k in local}
    def provider(ref,owner,siblings_only=False):
        for g in ([] if siblings_only else games):
            target=g.find(ref,owner)
            if target is not None: return g,target
        if siblings is not None:
            folder=siblings.locate(ref,owner,exclude)
            if folder is not None:
                folder=Path(folder).resolve()
                if folder not in pool: pool[folder]=GameResources(folder,cancel)
                g=pool[folder];target=g.find(ref,owner)
                if target is not None: return g,target
        return None,None
    imported={};origin={};pending=[(rel,path.read_bytes()) for rel,path in local.items() if path.suffix.lower() in {'.mdl','.msh','.mtl','.ani','.lua'}]
    visited=set()
    if siblings is not None:
        overrides,conflicts=sound_overrides(local,siblings,exclude)
        info['sound_override_conflicts']=[(rel,[Path(f).name for f in folders]) for rel,folders in conflicts]
        for rel,folder in overrides.items():
            if folder not in pool: pool[folder]=GameResources(folder,cancel)
            addon=pool[folder];target=addon.names.get(rel.casefold())
            if target is None: continue
            content=addon.read(target)
            imported[target]=content;origin[target]=addon
            info['game_soundsets'][rel]={'text':content.decode('utf-8-sig'),'source':str(addon.index[target][0]),'provider':addon.root.name}
            info['sound_overrides'][rel]=addon.root.name
            pending.append((target,content))
            # the add-on's own recordings beat the MOD's same-named ones and add the ones the MOD lacks
            for name in literals(content.decode('utf-8-sig'),rel):
                if name.lower().endswith('.wav'):
                    audio=addon.names.get(('res/audio/effects/'+name.lstrip('/')).casefold())
                    if audio is not None and audio not in imported: imported[audio]=addon.read(audio);origin[audio]=addon
    while pending:
        if cancelled(cancel): raise InterruptedError('ゲーム資産の収集を取り消しました。')
        owner,raw=pending.pop()
        if owner in visited: continue
        visited.add(owner)
        if len(raw)>16*1024**2: raise ValueError('ゲーム依存定義は16MiBまでです。')
        text=raw.decode('utf-8-sig')
        refs=[v for v in literals(text,owner) if PurePosixPath(v).suffix.lower() in PREFIXES]
        if owner.startswith('res/models/model/') and owner.endswith('.mdl') and owner in local:
            # Repaints may ship the model while their base MOD supplies its
            # colour-specific UI images. Match full paths, never basenames.
            stem=owner[len('res/models/model/'):-len('.mdl')]
            for icon_root in ('res/textures/ui/models_20/', 'res/textures/ui/models_small/'):
                for suffix in ('@2x.tga', '.tga', '_cblend@2x.tga', '_cblend.tga'):
                    ref=icon_root+stem+suffix
                    if ref.casefold() in local_lookup: continue
                    game,icon=provider(ref,owner)
                    if icon is not None and icon not in imported:
                        imported[icon]=game.read(icon);origin[icon]=game
            if len(imported)>25000 or sum(map(len,imported.values()))>12*1024**3:
                raise ValueError('ゲーム資産の収集上限を超えました。')
        if owner.endswith('.mdl'):
            try:
                model_text, _ = repair_model_text(text, owner)
                data=parse(model_text,owner)
                sound=data.get('metadata',{}).get('railVehicle',{}).get('soundSet',{})
                sound=(sound if isinstance(sound,str) else sound.get('name','') if isinstance(sound,dict) else '')
                sound=sound.strip() if isinstance(sound,str) else ''
                if sound: refs.append('res/config/sound_set/'+safe_relative(sound.removesuffix('.lua'))+'.lua')
            except LuaParseError: pass
        scripts=module_scripts(text) if owner.endswith(('.mdl','.lua')) else []
        for ref in refs+scripts:
            if ref.startswith('::/'): continue
            if resolve_indexed(ref,owner,local_lookup) is not None: continue
            # A MOD's Lua libraries (`require "a.b"`) come only from other MODs; the game's own scripts are never copied.
            game,target=provider(ref,owner,siblings_only=ref in scripts)
            if target is None or target in imported or target in info['game_soundsets']: continue
            content=game.read(target)
            origin[target]=game
            if target.startswith('res/config/sound_set/') and target.endswith('.lua'):
                info['game_soundsets'][target]={'text':content.decode('utf-8-sig'),'source':str(game.index[target][0]),'provider':game.root.name}
                pending.append((target,content));continue
            imported[target]=content
            if target.startswith('res/models/model/') and target.endswith('.mdl'):
                # Store images are implicit companions, absent from Lua refs.
                # Take only this model's images from the same provider; keep
                # the source MOD's own images and unrelated models untouched.
                stem=target[len('res/models/model/'):-len('.mdl')]
                for icon_root in ('res/textures/ui/models_20/', 'res/textures/ui/models_small/'):
                    for suffix in ('@2x.tga', '.tga', '_cblend@2x.tga', '_cblend.tga'):
                        icon=game.names.get((icon_root+stem+suffix).casefold())
                        if icon is not None and icon.casefold() not in local_lookup and icon not in imported:
                            imported[icon]=game.read(icon);origin[icon]=game
            if target.endswith('.msh'):
                blob=game.names.get((target+'.blob').casefold())
                if blob and resolve_indexed(blob,owner,local_lookup) is None and blob not in imported:
                    imported[blob]=game.read(blob);origin[blob]=game
            if len(imported)>25000 or sum(map(len,imported.values()))>12*1024**3:
                raise ValueError('ゲーム依存資産の収集上限を超えました。')
            if PurePosixPath(target).suffix in {'.mdl','.msh','.mtl','.ani','.lua'}:
                pending.append((target,content))
            notify(progress,'game_assets',len(imported),len(imported),target)
    if not imported:
        yield root,info;return
    with tempfile.TemporaryDirectory(prefix='tpf2_game_overlay_') as temporary:
        overlay=Path(temporary)
        replaced={rel.casefold() for rel in imported}
        for rel,source in local.items():
            if cancelled(cancel): raise InterruptedError('変換を取り消しました。')
            if rel.casefold() in replaced: continue
            target=overlay/rel;target.parent.mkdir(parents=True,exist_ok=True)
            try: os.link(source,target)
            except OSError: shutil.copyfile(source,target)
        for rel,raw in imported.items():
            target=overlay/rel;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(raw)
            game=origin[rel];archive,member=game.index[rel]
            info['game_dependencies'][rel]={'source':str(archive),'archive_member':member,'provider':game.root.name,
                                           'size':len(raw),'sha256':sha256(target,cancel)}
            info['imported_from'][game.root.name]=info['imported_from'].get(game.root.name,0)+1
        yield overlay,info
