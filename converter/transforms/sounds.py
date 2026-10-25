"""Package and validate the bounded reference-based sound profile."""
from pathlib import Path
import hashlib
import json
import re
from converter.adapters.tpf3_writer import generated_sound_name, GAME_SOUNDSETS
from converter.resolver import array
from converter.adapters.tpf2_reader import diagnostic
from converter.parsers.lua import parse, dumps
from converter.parsers.lua import _Parser
from converter.resolver import resolve
from converter.tpf3_sounds import tpf3_shared_sound, stock_audio_for
from converter.transforms.sound_tracks import is_tracks_soundset, convert_tracks_soundset

_SOUND_CALLS={'makeSoundSet','addTrackParam01','addSimpleTrackParam01','addEventParam01','addTrackCustom','addTrackSqueal','addTrackBrake','addEventClacks','addEvent'}
# TPF2 passes the sampled value as a bare name ("speed01"); TPF3 wants {info key, name}. Value = index of the first such argument.
_TPF2_PARAM_ARGS={'addTrackParam01':5,'addSimpleTrackParam01':3,'addEventParam01':6}
SOUND_DIR='_assets/generated/sound'
def check_game_sound_code(text):
    tokens=_Parser(text,'sound script').tokens
    forbidden={'os','io','debug','load','loadstring','dofile','_G','setmetatable','getmetatable'}
    if any(t.kind=='name' and t.value in forbidden for t in tokens):
        raise ValueError('外部処理を含むサウンドLuaは自動移植できません。')
    calls=set(re.findall(r'\bsoundsetutil\s*\.\s*(\w+)\s*\(',text))
    if not calls:
        raise ValueError('soundsetutilの呼び出しを使わない形式のサウンド定義です（自動移植の対象外）。')
    if calls-_SOUND_CALLS:
        raise ValueError(f'未確認のsoundsetutil API: {sorted(calls-_SOUND_CALLS)}')
    imports=re.findall(r'\brequire\s*\(?\s*["\']([^"\']+)["\']',text)
    if any(s not in {'soundsetutil','audioutil','::/scripts/soundsetutil.lua','::/scripts/audioutil.lua'} for s in imports):
        raise ValueError('独自requireを含むサウンドセットは手動移植が必要です。')
    return tokens

def convert_game_sound(text,owner,files,mapping,fallback=None):
    tokens=check_game_sound_code(text)
    replacements=[]
    for i,t in enumerate(tokens[:-1]):
        if t.kind!='string': continue
        value=t.value
        if value in {'soundsetutil','audioutil'}:
            value='::/scripts/'+value+'.lua'
        elif value.lower().endswith('.wav'):
            source=resolve(value,owner,files)
            if source in mapping:
                value='/'+mapping[source]
            elif tpf3_shared_sound(value):
                value=tpf3_shared_sound(value)  # a TPF2 stock sound: TPF3 ships the same file
            elif fallback is not None: value=fallback(value)  # one unresolvable file becomes a stock sound; the rest stays
            else: raise ValueError(f'音声を解決できません: {value}')
        else: continue
        replacements.append((t.offset,tokens[i+1].offset,json.dumps(value,ensure_ascii=False)+' '))
    replacements+=_param_replacements(tokens)
    replacements+=_clack_weight_replacements(tokens)
    for start,end,value in sorted(replacements,key=lambda r:r[0],reverse=True): text=text[:start]+value+text[end:]
    return text

def _clack_weight_replacements(tokens):
    """`addEventClacks(data, names, refDist, 10.0)`: TPF2 states the reference axle weight in tonnes, TPF3 in kilograms.

    Left as 10.0 the axle weight (kg) is always above it and every car clacks at full volume instead of fading with its weight.
    A value that is not a plain number (e.g. `10.0 * 1000.0` written for TPF3) is left alone.
    """
    from converter.transforms.sound_tracks import _close,_pieces,_number,KG_PER_TONNE
    out=[]
    for i in range(len(tokens)-3):
        t=tokens[i]
        if not(t.kind=='name' and t.value=='addEventClacks' and tokens[i+1].value=='(' and i>=2 and tokens[i-1].value=='.'): continue
        pieces=_pieces(tokens,i+1,_close(tokens,i+1))
        if len(pieces)>3:
            a,b=pieces[3]
            if b-a==1 and tokens[a].kind=='number':
                out.append((tokens[a].offset,tokens[a+1].offset,_number(float(tokens[a].value)*KG_PER_TONNE)+' '))
    return out

def _param_replacements(tokens):
    """`addTrackParam01(..., "speed01")` -> `addTrackParam01(..., { "vehicle", "speed01" })`: TPF3 looks the value up as info[1][2]."""
    from converter.transforms.sound_tracks import _close,_pieces
    out=[]
    for i in range(len(tokens)-3):
        t=tokens[i]
        if not(t.kind=='name' and t.value in _TPF2_PARAM_ARGS and tokens[i+1].value=='(' and i>=2 and tokens[i-1].value=='.' ): continue
        close=_close(tokens,i+1)
        for index,(a,b) in enumerate(_pieces(tokens,i+1,close)):
            if index>=_TPF2_PARAM_ARGS[t.value] and b-a==1 and tokens[a].kind=='string':
                out.append((tokens[a].offset,tokens[a+1].offset,'{ "vehicle", '+json.dumps(tokens[a].value)+' } '))
    return out

def stock_soundset(model):
    """Key of TPF3's own sound set that fits the vehicle: by its engine type; unpowered cars sound like waggons."""
    engines=array(model.get('metadata',{}).get('landVehicle',{}).get('engines',{}))
    types={e.get('type') for e in engines if isinstance(e,dict)}
    if not engines: return 'waggon_modern'
    if 'DIESEL' in types: return 'train_diesel'
    if 'STEAM' in types: return 'train_steam_modern'
    return 'train_electric_modern'

def write_soundsets(package,analysis=None,mapping=None):
    content=Path(package)/'content'
    try: mod_id=json.loads((Path(package)/'mod.json').read_text('utf-8-sig'))['modId']
    except (OSError,ValueError,KeyError): mod_id=Path(package).name
    needed=set();engines_of={}
    for p in content.rglob('*.mdl'):
        model=parse(p.read_text('utf-8'),str(p))
        name=model.get('metadata',{}).get('soundConfig',{}).get('soundSet',{}).get('name','')
        if name.startswith('/_assets/generated/sound/'):
            needed.add(Path(name).name+'.lua')
            engines_of.setdefault(Path(name).name+'.lua',set()).update(
                e.get('type') for e in array(model.get('metadata',{}).get('landVehicle',{}).get('engines',{})) if isinstance(e,dict))
    # TPF2 sound set scripts that were collected from the MOD itself, its dependencies or the game
    originals={generated_sound_name(k.removeprefix('res/config/sound_set/').removesuffix('.lua')):k
               for k in (analysis or {}).get('game_soundsets',{})}
    diagnostics=[];imported_scripts={};failed=set()
    for name in sorted(needed):
        stem=name.removesuffix('.snd.lua')
        original=originals.get(stem) or 'res/config/sound_set/'+stem+'.lua'
        game_sound=(analysis or {}).get('game_soundsets',{}).get(original)
        if not game_sound: raise ValueError(f'Unknown generated sound: {name}')
        target=content/'_assets/generated/sound'/name
        target.parent.mkdir(parents=True,exist_ok=True)
        replaced=[]
        def fallback(reference,_types=engines_of.get(name,set())):
            replacement=stock_audio_for(reference,_types)
            replaced.append((reference,replacement))
            return replacement
        if game_sound:
            try:
                if is_tracks_soundset(game_sound['text']):
                    script_name=stem+'.script.lua'
                    converted,callbacks=convert_tracks_soundset(game_sound['text'],original,analysis['files'],mapping or {},
                                                                 mod_id,SOUND_DIR+'/'+stem+'.script',fallback)
                    script_raw=callbacks.encode('utf-8')
                    imported_scripts[SOUND_DIR+'/'+script_name]=hashlib.sha256(script_raw).hexdigest()
                else:
                    converted=convert_game_sound(game_sound['text'],original,analysis['files'],mapping or {},fallback)
                    script_raw=None
                raw=converted.encode('utf-8')
                target.write_bytes(raw)  # bytes, so Windows newline translation cannot change what was hashed
                if script_raw is not None: (target.parent/script_name).write_bytes(script_raw)
                imported_scripts[target.relative_to(content).as_posix()]=hashlib.sha256(raw).hexdigest()
                diagnostics.append(diagnostic('GAME_SOUNDSET_IMPORTED','サウンド定義と元音源を取り込みました（'+(game_sound.get('provider') or '元MOD')+'）。',original,severity='info'))
                if replaced:
                    shown='、'.join(f'{ref} → {rep}' for ref,rep in replaced[:4])+(f' ほか{len(replaced)-4}件' if len(replaced)>4 else '')
                    diagnostics.append(diagnostic('SOUND_FILE_REPLACED_BY_STOCK',
                        f'元MODに見つからない音声 {len(replaced)} 件を、同じ種類のTPF3標準の音声に差し替えました（元の音ではありません）。残りの音は元のMODのものです: {shown}',
                        original,severity='warning'))
            except ValueError as exc:
                failed.add('/_assets/generated/sound/'+stem+'.snd')
                diagnostics.append(diagnostic('GAME_SOUNDSET_UNSUPPORTED',f'{exc} 代わりにTPF3標準のサウンドを使います（元MODの音ではありません）。',original,severity='warning'))
    if failed:
        # A model naming a sound set that was not written would be removed by TPF3: use TPF3's own set for its kind.
        for p in content.rglob('*.mdl'):
            model=parse(p.read_text('utf-8'),str(p))
            config=model.get('metadata',{}).get('soundConfig',{})
            if config.get('soundSet',{}).get('name') in failed:
                model['metadata']['soundConfig']={'soundSet':{'name':GAME_SOUNDSETS[stock_soundset(model)]}}
                p.write_text(dumps(model),'utf-8')
    if imported_scripts:
        target=Path(package)/'_metadata/imported_sound_scripts.json'
        target.parent.mkdir(parents=True,exist_ok=True)
        target.write_text(json.dumps(imported_scripts,indent=2),'utf-8')
    return diagnostics

def check_callback_script(text):
    tokens=_Parser(text,'sound callbacks').tokens
    forbidden={'os','io','debug','load','loadstring','dofile','_G','setmetatable','getmetatable'}
    if any(t.kind=='name' and t.value in forbidden for t in tokens):
        raise ValueError('外部処理を含むサウンドコールバックです。')
    if any(s!='::/scripts/audioutil.lua' for s in re.findall(r'\brequire\s*\(?\s*["\']([^"\']+)["\']',text)):
        raise ValueError('未確認のrequireを含むサウンドコールバックです。')

def check_callback_references(content,path,text):
    """Every `x.script@fn` reference must name this MOD and an existing function.

    TPF3 resolves a reference without `<modId>::` against the base game ("Resource not found"), so the
    callback silently never runs and the track stays mute. Checking that the file exists is not enough.
    """
    try: mod_id=json.loads((content.parent/'mod.json').read_text('utf-8-sig'))['modId']
    except (OSError,ValueError,KeyError): mod_id=''
    found=[]
    for reference in re.findall(r'"([^"\n]*\.script@[^"\n]*)"',text):
        prefix,_,rest=reference.partition('::')
        where,_,function=rest.partition('@') if rest else ('','','')
        if not mod_id or prefix!=mod_id or not where.startswith('/') or not function:
            found.append(diagnostic('SOUND_CALLBACK_REF_INVALID',f'サウンドの関数参照に「{mod_id}::」がありません（TPF3が本体側を探して見つからなくなります）: {reference}',str(path)))
            continue
        script=content/(where.lstrip('/')+'.lua')
        if not script.resolve().is_relative_to(content.resolve()) or not script.is_file():
            found.append(diagnostic('SOUND_CALLBACK_FILE_MISSING',f'サウンドの関数参照先のファイルがありません: {reference}',str(path)))
        elif not re.search(r'\bresult\.'+re.escape(function)+r'\s*=\s*function',script.read_text('utf-8')):
            found.append(diagnostic('SOUND_CALLBACK_FUNCTION_MISSING',f'サウンドの関数参照先に関数がありません: {reference}',str(path)))
    return found

def validate_sound_script(content,path):
    """Accept a generated sound script only if it is the one the converter wrote.

    Input Lua is never executed. The converter records the hash of every sound set
    script it converted; anything else in the generated folder is reported, and
    other dynamic scripts still receive the ordinary OUTPUT_PARSE_ERROR. This is
    not a general Lua validation bypass.
    """
    if path.parent != content/SOUND_DIR or not path.name.endswith(('.snd.lua','.script.lua')):
        return None
    diagnostics=[]
    raw=path.read_bytes()
    text=raw.decode('utf-8')
    is_callbacks=path.name.endswith('.script.lua')
    if True:
        manifest=content.parent/'_metadata/imported_sound_scripts.json'
        try:
            saved=json.loads(manifest.read_text('utf-8'))
            if saved.get(path.relative_to(content).as_posix()) != hashlib.sha256(raw).hexdigest(): raise ValueError('hash mismatch')
            if is_callbacks: check_callback_script(text)
            else: check_game_sound_code(text)
        except (ValueError,OSError):
            return [diagnostic('GENERATED_SOUND_SCRIPT_CHANGED','生成サウンドスクリプトがテンプレート/取り込み記録と一致しません。',str(path))]
    if is_callbacks: return diagnostics
    diagnostics.extend(check_callback_references(content,path,text))
    for reference in re.findall(r'"([^"\n]+\.wav)"',text):
        if reference.startswith('::/'): continue
        local=(content/reference.lstrip('/')).resolve()
        if not local.is_relative_to(content.resolve()) or not local.is_file():
            diagnostics.append(diagnostic('SOUND_AUDIO_MISSING',f'音声がありません: {reference}',str(path)))
        else:
            with local.open('rb') as handle: header=handle.read(12)
            if header[:4] != b'RIFF' or header[8:12] != b'WAVE':
                diagnostics.append(diagnostic('SOUND_AUDIO_INVALID','音声がRIFF/WAVE形式ではありません。',str(local)))
    return diagnostics
