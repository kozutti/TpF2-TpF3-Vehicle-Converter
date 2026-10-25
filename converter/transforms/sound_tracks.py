"""TPF2 sound sets written as `tracks` / `events` / `updateFn` -> TPF3 `addTrackCustom` plus a callback script.

TPF2 evaluates `updateFn(input)` every frame and returns a gain/pitch for each track. TPF3 takes a
reference to a script function instead, so each track becomes `result.track_N` in a script next to the
sound set. The reference must carry the MOD id (`<modId>::/path/x.script@track_N`): without it TPF3
looks the script up in the base game and fails with "Resource not found" (the track then never sounds).
Input Lua is never executed; the expressions are rewritten token by token from a small allowed set.
"""
import re
from converter.parsers.lua import _Parser
from converter.resolver import resolve
from converter.tpf3_sounds import tpf3_shared_sound

# TPF2 `input.<name>` -> the value TPF3 passes to sound callbacks: (table of `currentInfo`, key, value when absent, divisor).
# Checked in the game on a stock electric locomotive with a sound probe: brakeDecel is 0 while coasting and
# equals the smoothed value while braking, weight is in kg (TPF2: tonnes), numAxles 4, gameSpeedUp 1, topSpeed in m/s.
# powerOutput: TPF3 reports kW; the MODs that read TPF2's value (some sound sets) feed it to curves over -1..1, so TPF2's is the
# signed output relative to the rated power. TPF3 itself normalises it that way (`powerOutput / power`, transformator_util) and
# measured power01 == powerOutput / power (max error 5e-5). Not measured in TPF2 itself (TPF2 does not start on this PC).
# Not mapped on purpose: curveRadius/curveSpeedLimit (not measured).
POWER_OUTPUT = '((vehicle.power or 0) > 0 and (vehicle.powerOutput or 0) / vehicle.power or 0)'
INPUTS = {
    'speed01': ('vehicle', 'speed01', 0, 1), 'power01': ('vehicle', 'power01', 0, 1),
    'brakeDecel': ('vehicle', 'brakeDecel', 0, 1), 'speed': ('vehicle', 'speed', 0, 1),
    'topSpeed': ('vehicle', 'topSpeed', 1, 1),
    'sideForce': ('railVehicle', 'sideForce', 0, 1), 'maxSideForce': ('railVehicle', 'maxSideForce', 0, 1),
    'numAxles': ('railVehicle', 'numAxles', 1, 1), 'gameSpeedUp': ('railVehicle', 'gameSpeedUp', 1, 1),
    'chuffStep': ('railVehicle', 'chuffStep', 0, 1), 'axleRefWeight': ('railVehicle', 'axleRefWeight', 10, 1),
    'weight': ('railVehicle', 'weight', 0, 1000),
}
MATH = {'min', 'max', 'abs'}
KG_PER_TONNE = 1000.0
_SYMBOLS = {'{', '}', ',', '(', ')', '+', '-', '*', '/'}
_PAIRS = {'{': '}', '(': ')', '[': ']'}


def is_tracks_soundset(text):
    return bool(re.search(r'\bupdateFn\b', text) and re.search(r'\btracks\s*=\s*\{', text))


def _close(tokens, start):
    stack = []
    for j in range(start, len(tokens)):
        t = tokens[j]
        if t.kind != 'symbol':
            continue
        if t.value in _PAIRS:
            stack.append(_PAIRS[t.value])
        elif t.value in _PAIRS.values():
            if not stack or stack.pop() != t.value:
                break
            if not stack:
                return j
    raise ValueError('括弧が対応していないサウンド定義です。')


def _pieces(tokens, start, end):
    """Top-level comma separated token ranges strictly between start and end."""
    out, depth, first = [], 0, start + 1
    for j in range(start + 1, end):
        t = tokens[j]
        if t.kind == 'symbol' and t.value in _PAIRS:
            depth += 1
        elif t.kind == 'symbol' and t.value in _PAIRS.values():
            depth -= 1
        elif t.kind == 'symbol' and t.value in {',', ';'} and depth == 0:
            if j > first:
                out.append((first, j))
            first = j + 1
    if end > first:
        out.append((first, end))
    return out


def _table_after(tokens, key, lo=0, hi=None):
    """(open, close) of the table in `key = { ... }` found between lo and hi, or None."""
    for j in range(lo, (hi if hi is not None else len(tokens)) - 2):
        if tokens[j].kind == 'name' and tokens[j].value == key and tokens[j + 1].value == '=' and tokens[j + 2].value == '{':
            return j + 2, _close(tokens, j + 2)
    return None


def _fields(tokens, start, end):
    """`key = value` pieces of a table -> {key: (value start, value end)}."""
    fields = {}
    for a, b in _pieces(tokens, start, end):
        if b - a >= 3 and tokens[a].kind == 'name' and tokens[a + 1].value == '=':
            fields[tokens[a].value] = (a + 2, b)
    return fields


def _scalar(tokens, rng, kind):
    a, b = rng
    if b - a != 1 or tokens[a].kind != kind:
        raise ValueError('サウンド定義の値が定数ではありません。')
    return tokens[a].value


def _strings(tokens, start, end):
    names = []
    for a, b in _pieces(tokens, start, end):
        if b - a == 1 and tokens[a].kind == 'string':
            names.append(tokens[a].value)
        elif b - a == 5 and tokens[a].value == '[' and tokens[a + 2].value == ']' and tokens[a + 3].value == '=' and tokens[b - 1].kind == 'string':
            names.append(tokens[b - 1].value)
        else:
            raise ValueError('サウンド定義の音声一覧が定数ではありません。')
    return names


def _number(value):
    return repr(float(value)) if isinstance(value, float) else str(value)


def _expression(tokens, a, b):
    """Lua text of tokens[a:b] with TPF2's sound helpers mapped to TPF3's; anything unknown is refused."""
    out, j = [], a
    while j < b:
        t = tokens[j]
        if t.kind == 'number':
            out.append(_number(t.value))
        elif t.kind == 'symbol' and t.value in _SYMBOLS:
            out.append(t.value)
        elif t.kind == 'name' and j + 2 < b and tokens[j + 1].value == '.' and tokens[j + 2].kind == 'name':
            member = tokens[j + 2].value
            if t.value == 'soundeffectsutil' and member == 'sampleCurve':
                out.append('audioutil.sampleCurve')
            elif t.value == 'input' and member == 'powerOutput':
                out.append(POWER_OUTPUT)
            elif t.value == 'input' and member in INPUTS:
                table, key, default, divisor = INPUTS[member]
                value = f'({table}.{key} or {default})'
                out.append(value if divisor == 1 else f'({value} / {divisor})')
            elif t.value == 'math' and member in MATH:
                out.append(f'math.{member}')
            else:
                raise ValueError(f'未対応のサウンド式: {t.value}.{member}')
            j += 2
        else:
            raise ValueError(f'未対応のサウンド式: {t.value}')
        j += 1
    text = ' '.join(out)
    text = re.sub(r'(sampleCurve|math\.\w+) \(', r'\1(', text)
    return re.sub(r'\s+([,)}])', r'\1', re.sub(r'([({])\s+', r'\1', text))


def _event_overrides(tokens, update):
    """`events = { closeDoors = { gain = 3.0, pitch = 0.9 } }` in updateFn's result -> {event: (gain, pitch)}.

    TPF2 re-reads these every frame and applies them to the event the game fires (doors, horn). Only constants
    that differ from the default are returned; an expression is left out (the event keeps TPF3's gain 1 / pitch 1).
    """
    table = _table_after(tokens, 'events', update) if update is not None else None
    result = {}
    if not table:
        return result
    for key, (a, b) in _fields(tokens, *table).items():
        if key == 'clacks' or tokens[a].value != '{':
            continue
        sub = _fields(tokens, a, _close(tokens, a))
        values = {}
        for name in ('gain', 'pitch'):
            if name not in sub:
                values[name] = 1.0
                continue
            lo, hi = sub[name]
            if hi - lo != 1 or tokens[lo].kind != 'number':
                values = None
                break
            values[name] = float(tokens[lo].value)
        if values and (values['gain'] != 1.0 or values['pitch'] != 1.0):
            result[key] = (values['gain'], values['pitch'])
    return result


def _wav(value, owner, files, mapping, fallback=None):
    source = resolve(value, owner, files)
    if source in mapping:
        return '/' + mapping[source]
    shared = tpf3_shared_sound(value)
    if shared:
        return shared  # a TPF2 stock sound: TPF3 ships the same file
    if fallback is not None:
        return fallback(value)  # keep the rest of the set; this one file becomes a stock sound
    raise ValueError(f'音声を解決できません: {value}')


def _lua_table(rows, indent):
    return '{\n' + ''.join(f'{indent}  {{ {a}, {b} }},\n' for a, b in rows) + indent + '}'


def convert_tracks_soundset(text, owner, files, mapping, mod_id, script_path, fallback=None):
    """-> (sound set Lua, callback script Lua). `script_path` is the script's path inside the package."""
    tokens = _Parser(text, 'sound set').tokens[:-1]
    forbidden = {'os', 'io', 'debug', 'load', 'loadstring', 'dofile', '_G', 'setmetatable', 'getmetatable'}
    imports = re.findall(r'\brequire\s*\(?\s*["\']([^"\']+)["\']', text)
    if any(t.kind == 'name' and t.value in forbidden for t in tokens) or any(s not in {'soundeffectsutil', 'audioutil'} for s in imports):
        raise ValueError('外部処理を含むサウンドLuaは自動移植できません。')
    static = _table_after(tokens, 'tracks')
    update = next((j for j, t in enumerate(tokens) if t.kind == 'name' and t.value == 'updateFn'), None)
    dynamic = _table_after(tokens, 'tracks', update) if update is not None else None
    if static is None or dynamic is None:
        raise ValueError('tracks と updateFn の組になっていないサウンド定義です。')
    declared = []
    for a, b in _pieces(tokens, *static):
        if tokens[a].value != '{':
            raise ValueError('サウンド定義のtracksが定数ではありません。')
        fields = _fields(tokens, a, _close(tokens, a))
        declared.append((_scalar(tokens, fields['name'], 'string'), float(_scalar(tokens, fields['refDist'], 'number'))))
    live = _pieces(tokens, *dynamic)
    if len(live) < len(declared):
        raise ValueError(f'tracksの数({len(declared)})とupdateFnが返す数({len(live)})が違います。')
    live = live[:len(declared)]  # TPF2 pairs them by position and ignores values beyond the declared tracks

    weight = 10.0
    for j in range(update, len(tokens) - 2):
        if tokens[j].value == 'axleRefWeight' and tokens[j + 1].value == '=' and tokens[j + 2].kind == 'number':
            weight = float(tokens[j + 2].value)
            break

    lines = ['local soundsetutil = require "::/scripts/soundsetutil.lua"', '', 'function data()',
             '  local data = soundsetutil.makeSoundSet()']
    functions, count = [], 0
    for (name, ref_dist), (a, b) in zip(declared, live):
        wav = _wav(name, owner, files, mapping, fallback)
        if tokens[a].value == '{':
            fields = _fields(tokens, a, _close(tokens, a))
            count += 1
            gain = _expression(tokens, *fields['gain']) if 'gain' in fields else '1'
            pitch = _expression(tokens, *fields['pitch']) if 'pitch' in fields else '1'
            functions.append(f'result.track_{count} = function(output, customParams, currentInfo)\n'
                             f'  local vehicle = currentInfo.vehicle or {{}}\n'
                             f'  local railVehicle = currentInfo.railVehicle or {{}}\n'
                             f'  output.gain = {gain}\n  output.pitch = {pitch}\nend\n')
            ref = f'{mod_id}::/{script_path}@track_{count}'
            lines.append(f'  soundsetutil.addTrackCustom(data, "{wav}", {_number(ref_dist)}, '
                         f'{_lua_table([(0, 1)], "  ")}, {_lua_table([(0, 1)], "  ")}, '
                         '{ "vehicle", "speed01" }, { "vehicle", "speed01" }, '
                         f'"{ref}")')
        elif tokens[a].value == 'soundeffectsutil' and tokens[a + 2].value == 'brake':
            args = _pieces(tokens, a + 3, b - 1)
            strength = _scalar(tokens, args[2], 'number') if len(args) == 3 else 0.5
            lines.append(f'  soundsetutil.addTrackBrake(data, "{wav}", {_number(ref_dist)}, {_number(strength)})')
        elif tokens[a].value == 'soundeffectsutil' and tokens[a + 2].value == 'squeal':
            lines.append(f'  soundsetutil.addTrackSqueal(data, "{wav}", {_number(ref_dist)})')
        else:
            raise ValueError('updateFnのtracksに未対応の要素があります。')

    events = _table_after(tokens, 'events')
    overrides = _event_overrides(tokens, update)
    if events:
        for key, (a, b) in _fields(tokens, *events).items():
            sub = _fields(tokens, a, b - 1) if tokens[a].value == '{' else {}
            if 'names' not in sub or tokens[sub['names'][0]].value != '{':
                raise ValueError(f'サウンドイベント {key} の音声一覧が読めません。')
            names = [_wav(v, owner, files, mapping, fallback) for v in _strings(tokens, sub['names'][0], _close(tokens, sub['names'][0]))]
            listing = '{\n' + ''.join(f'    [{i}] = "{v}",\n' for i, v in enumerate(names, 1)) + '  }'
            dist = _number(float(_scalar(tokens, sub['refDist'], 'number'))) if 'refDist' in sub else '15.0'
            if key == 'clacks':
                lines.append(f'  soundsetutil.addEventClacks(data, {listing}, {dist}, {_number(weight * KG_PER_TONNE)})')
            elif key in overrides:
                gain, pitch = overrides[key]
                lines.append(f'  soundsetutil.addEventParam01(data, "{key}", {listing}, {dist}, '
                             f'{{ {{ 0, {_number(gain)} }} }}, {{ {{ 0, {_number(pitch)} }} }}, {{ "vehicle", "speed01" }})')
            else:
                lines.append(f'  soundsetutil.addEvent(data, "{key}", {listing}, {dist})')
    lines += ['  return data', 'end', '']
    script = ('local audioutil = require "::/scripts/audioutil.lua"\n\nlocal result = {}\n\n'
              + '\n'.join(functions) + '\nfunction data()\n  return result\nend\n\nreturn result\n')
    return '\n'.join(lines), script
