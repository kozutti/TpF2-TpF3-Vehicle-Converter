"""Run a MOD's own Lua in a locked-down interpreter when the constant-data parser cannot read a resource.

Some TPF2 MODs generate their models with Lua libraries shipped inside the MOD (`require "author_lib.model_a"`,
seat helpers, loops with function calls ...). The declarative parser refuses those. As a fallback, such a file is
evaluated here: no file, process, network or debug access exists inside the sandbox, `require` only reads files of
the MOD itself (plus small stand-ins for TPF2's `transf`, `vec2`, `vec3`, `vec4`, `tableutil`, `vehicleutil`), and the
run is cut off after a fixed number of VM instructions. The result is turned into the same plain tables the parser
returns, so everything after reading is unchanged.
"""
from __future__ import annotations

import math

INSTRUCTION_LIMIT = 200_000_000
MAX_DEPTH = 64

try:  # the optional runtime; without it the fallback is simply unavailable
    from lupa.luajit21 import LuaRuntime  # type: ignore
except Exception:  # pragma: no cover - depends on the installed wheel
    try:
        from lupa import LuaRuntime  # type: ignore
    except Exception:
        LuaRuntime = None


class SandboxError(ValueError):
    pass


def available() -> bool:
    return LuaRuntime is not None


_VEC4 = '''
local vec4 = {}
function vec4.new(x, y, z, w) return { x = x, y = y, z = z, w = w } end
return vec4
'''

_VEC3 = '''
local vec3 = {}
local mt
function vec3.new(x, y, z) return setmetatable({ x = x, y = y, z = z }, mt) end
mt = {
  __add = function(a, b) return vec3.new(a.x + b.x, a.y + b.y, a.z + b.z) end,
  __sub = function(a, b) return vec3.new(a.x - b.x, a.y - b.y, a.z - b.z) end,
  __mul = function(f, v) return vec3.new(f * v.x, f * v.y, f * v.z) end,
}
function vec3.add(a, b) return a + b end
function vec3.sub(a, b) return a - b end
function vec3.mul(f, v) return f * v end
function vec3.dot(a, b) return a.x * b.x + a.y * b.y + a.z * b.z end
function vec3.cross(a, b) return vec3.new(a.y * b.z - a.z * b.y, a.z * b.x - a.x * b.z, a.x * b.y - a.y * b.x) end
function vec3.length(v) return math.sqrt(v.x * v.x + v.y * v.y + v.z * v.z) end
function vec3.distance(a, b) return vec3.length(a - b) end
function vec3.normalize(v) local l = vec3.length(v) return vec3.new(v.x / l, v.y / l, v.z / l) end
return vec3
'''

_VEC2 = '''
local vec2 = {}
function vec2.new(x, y) return { x = x, y = y } end
function vec2.add(a, b) return { x = a.x + b.x, y = a.y + b.y } end
function vec2.sub(a, b) return { x = a.x - b.x, y = a.y - b.y } end
function vec2.mul(f, v) return { x = f * v.x, y = f * v.y } end
function vec2.div(v, f) return { x = v.x / f, y = v.y / f } end
function vec2.length(v) return math.sqrt(v.x * v.x + v.y * v.y) end
function vec2.distance(a, b) return vec2.length(vec2.sub(a, b)) end
function vec2.dot(a, b) return a.x * b.x + a.y * b.y end
function vec2.normalize(v) local l = vec2.length(v) return { x = v.x / l, y = v.y / l } end
function vec2.lerp(a, b, t) return { x = a.x + (b.x - a.x) * t, y = a.y + (b.y - a.y) * t } end
return vec2
'''

_TRANSF = '''
local vec3 = require "vec3"
local vec4 = require "vec4"
local transf = {}
function transf.new(c0, c1, c2, c3)
  return { c0.x, c0.y, c0.z, c0.w, c1.x, c1.y, c1.z, c1.w, c2.x, c2.y, c2.z, c2.w, c3.x, c3.y, c3.z, c3.w }
end
function transf.flipY(m)
  return { m[1], -m[2], m[3], m[4], m[5], -m[6], m[7], m[8], m[9], -m[10], m[11], m[12], m[13], -m[14], m[15], m[16] }
end
function transf.transl(t)
  return transf.new(vec4.new(1, 0, 0, 0), vec4.new(0, 1, 0, 0), vec4.new(0, 0, 1, 0), vec4.new(t.x, t.y, t.z, 1))
end
function transf.scale(t)
  return transf.new(vec4.new(t.x, 0, 0, 0), vec4.new(0, t.y, 0, 0), vec4.new(0, 0, t.z, 0), vec4.new(0, 0, 0, 1))
end
function transf.rotYCntTransl(rotY, cnt, transl)
  local sy, cy = math.sin(rotY), math.cos(rotY)
  local tx, ty, tz = cnt.x + transl.x, cnt.y + transl.y, cnt.z + transl.z
  return transf.new(vec4.new(cy, 0, -sy, 0), vec4.new(0, 1, 0, 0), vec4.new(sy, 0, cy, 0),
    vec4.new(-cy * cnt.x - sy * cnt.z + tx, -cnt.y + ty, sy * cnt.x - cy * cnt.z + tz, 1))
end
function transf.rotZ(a)
  local s, c = math.sin(a), math.cos(a)
  return transf.new(vec4.new(c, s, 0, 0), vec4.new(-s, c, 0, 0), vec4.new(0, 0, 1, 0), vec4.new(0, 0, 0, 1))
end
function transf.rotY(a)
  local s, c = math.sin(a), math.cos(a)
  return transf.new(vec4.new(c, 0, -s, 0), vec4.new(0, 1, 0, 0), vec4.new(s, 0, c, 0), vec4.new(0, 0, 0, 1))
end
function transf.rotX(a)
  local s, c = math.sin(a), math.cos(a)
  return transf.new(vec4.new(1, 0, 0, 0), vec4.new(0, c, s, 0), vec4.new(0, -s, c, 0), vec4.new(0, 0, 0, 1))
end
function transf.rotZTransl(a, t)
  local s, c = math.sin(a), math.cos(a)
  return transf.new(vec4.new(c, s, 0, 0), vec4.new(-s, c, 0, 0), vec4.new(0, 0, 1, 0), vec4.new(t.x, t.y, t.z, 1))
end
function transf.scaleRotZTransl(f, a, t)
  local s, c = math.sin(a), math.cos(a)
  return transf.new(vec4.new(f * c, f * s, 0, 0), vec4.new(f * -s, f * c, 0, 0), vec4.new(0, 0, f, 0), vec4.new(t.x, t.y, t.z, 1))
end
function transf.scaleXYZRotZTransl(f, a, t)
  local s, c = math.sin(a), math.cos(a)
  return transf.new(vec4.new(f.x * c, f.x * s, 0, 0), vec4.new(f.y * -s, f.y * c, 0, 0), vec4.new(0, 0, f.z, 0), vec4.new(t.x, t.y, t.z, 1))
end
function transf.rotZYXTransl(rot, t)
  local sx, sy, sz = math.sin(rot.z), math.sin(rot.y), math.sin(rot.x)
  local cx, cy, cz = math.cos(rot.z), math.cos(rot.y), math.cos(rot.x)
  return transf.new(vec4.new(cy * cz, cy * sz, -sy, 0),
    vec4.new(-cx * sz + sx * sy * cz, cx * cz + sx * sy * sz, sx * cy, 0),
    vec4.new(sx * sz + cx * sy * cz, -sx * cz + cx * sy * sz, cx * cy, 0), vec4.new(t.x, t.y, t.z, 1))
end
function transf.mul(a, b)
  local r = {}
  for col = 0, 3 do
    for row = 0, 3 do
      local sum = 0
      for k = 0, 3 do sum = sum + a[row + 4 * k + 1] * b[k + 4 * col + 1] end
      r[col * 4 + row + 1] = sum
    end
  end
  return r
end
function transf.scaleRotZYXTransl(f, rot, t) return transf.mul(transf.rotZYXTransl(rot, t), transf.scale(f)) end
function transf.degToRad(dx, dy, dz) return vec3.new(math.rad(dx), math.rad(dy), math.rad(dz)) end
return transf
'''

_TABLEUTIL = '''
local tableutil = {}
function tableutil.count(t) local n = 0 for _ in pairs(t) do n = n + 1 end return n end
return tableutil
'''

_VEHICLEUTIL = '''
local vec2 = require "vec2"
local transf = require "transf"
local vec3 = require "vec3"
local vehicleutil = {}
local function steps() return 16 end
local function wheelFrames(wheelCenter, wheelPos0, make)
  local radius = vec2.distance(wheelPos0, wheelCenter)
  local angle0 = math.atan2(wheelPos0.y - wheelCenter.y, wheelCenter.x - wheelPos0.x)
  local duration = 1000 * (2.0 * math.pi * wheelCenter.y)
  local frames = {}
  for i = 0, steps() do
    local f = i / steps()
    local angle = angle0 + 2.0 * math.pi * f
    local wheelPos = vec2.add(wheelCenter, vec2.mul(radius, vec2.new(-math.cos(angle), math.sin(angle))))
    frames[#frames + 1] = { time = duration * f, transf = make(wheelPos) }
  end
  return { type = "KEYFRAME_MATRIX", params = { keyframes = frames } }
end
function vehicleutil.makeCouplingRodAnim(wheelCenter, wheelPos0)
  return wheelFrames(wheelCenter, wheelPos0, function(p)
    return transf.transl(vec3.new(p.x - wheelPos0.x, 0, p.y - wheelPos0.y)) end)
end
function vehicleutil.makePistonAnim(wheelCenter, wheelPos0, pistonPos0)
  local length = vec2.distance(wheelPos0, pistonPos0)
  return wheelFrames(wheelCenter, wheelPos0, function(p)
    local dy = p.y - pistonPos0.y
    local dx = math.sqrt(length * length - dy * dy)
    return transf.transl(vec3.new(p.x + dx - pistonPos0.x, 0, 0)) end)
end
function vehicleutil.makeConnectingRodAnim(wheelCenter, wheelPos0, pistonPos0)
  local length = vec2.distance(wheelPos0, pistonPos0)
  local rodAngle0 = math.atan((wheelPos0.y - pistonPos0.y) / (pistonPos0.x - wheelPos0.x))
  return wheelFrames(wheelCenter, wheelPos0, function(p)
    local dy = p.y - pistonPos0.y
    local dx = math.sqrt(length * length - dy * dy)
    return transf.rotYCntTransl(math.atan(dy / dx) - rodAngle0,
      vec3.new(pistonPos0.x - wheelPos0.x, 0, pistonPos0.y - wheelPos0.y), vec3.new(p.x + dx - pistonPos0.x, 0, 0)) end)
end
return vehicleutil
'''

_BUILTIN = {'vec4': _VEC4, 'vec3': _VEC3, 'vec2': _VEC2, 'transf': _TRANSF, 'tableutil': _TABLEUTIL, 'vehicleutil': _VEHICLEUTIL}

_SETUP = '''
return function(read_module, limit)
jit.off()
local safe = { 'assert', 'error', 'ipairs', 'next', 'pairs', 'rawequal', 'rawget', 'rawset', 'select',
               'setmetatable', 'getmetatable', 'tonumber', 'tostring', 'type', 'unpack' }
local env = {}
for _, name in ipairs(safe) do env[name] = _G[name] end
local function copy(source, skip)
  local out = {}
  for k, v in pairs(source) do if not (skip and skip[k]) then out[k] = v end end
  return out
end
env.math = copy(math)
env.string = copy(string, { dump = true })
env.table = copy(table)
env.table.unpack = env.table.unpack or unpack
env._G = env
local pcall_, xpcall_ = pcall, xpcall
local function guard(ok, ...)
  if not ok then
    local e = ...
    if type(e) == 'string' and e:find('instruction limit reached', 1, true) then error(e, 0) end
  end
  return ok, ...
end
env.pcall = function(f, ...) return guard(pcall_(f, ...)) end
env.xpcall = function(f, h, ...) return guard(xpcall_(f, h, ...)) end
env._ = function(s) return s end
env.print = function() end
local loadstring_, setfenv_, tostring_ = loadstring, setfenv, tostring
local cache = {}
env.require = function(name)
  if cache[name] ~= nil then return cache[name] end
  local source = read_module(name)
  if source == nil then error("module '" .. tostring_(name) .. "' not found", 2) end
  local chunk, message = loadstring_(source, "=" .. name)
  if not chunk then error(message, 2) end
  setfenv_(chunk, env)
  cache[name] = true  -- a module requiring itself sees `true`, like Lua does
  local result = chunk()
  if result == nil then result = true end
  cache[name] = result
  return result
end
debug.sethook(function() error("instruction limit reached", 0) end, "", limit)
local function run(source, name)
  local chunk, message = loadstring_(source, "=" .. name)
  if not chunk then error(message, 0) end
  setfenv_(chunk, env)
  chunk()
  if type(env.data) ~= "function" then error("the file defines no data()", 0) end
  return env.data()
end
return run
end
'''


def _to_python(value, depth=0):
    if depth > MAX_DEPTH:
        raise SandboxError('table nesting is too deep')
    if hasattr(value, 'items') and not isinstance(value, (str, bytes)):
        result = {}
        for key, item in value.items():
            key = _number(key)
            if not isinstance(key, (int, float, str)):
                raise SandboxError('unsupported table key')
            converted = _to_python(item, depth + 1)
            if converted is not None:
                result[key] = converted
        return result
    if callable(value):
        raise SandboxError('functions cannot be part of a resource')
    return _number(value)


def _number(value):
    if isinstance(value, float):
        if not math.isfinite(value):
            raise SandboxError('a non-finite number is part of the result')
        if value == int(value) and abs(value) < 2 ** 53:
            return int(value)
    return value


def evaluate(text: str, filename: str, read_module) -> dict:
    """Evaluate a resource's data() in the sandbox. `read_module(name)` returns the Lua source of a MOD module or None."""
    if LuaRuntime is None:
        raise SandboxError('the Lua runtime (lupa) is not available')
    lua = LuaRuntime(unpack_returned_tuples=True, register_eval=False, register_builtins=False)

    def reader(name):
        name = str(name)
        return _BUILTIN.get(name) or read_module(name)

    try:
        factory = lua.execute(_SETUP)  # returns function(read_module, limit) -> run(source, name)
        runner = factory(reader, INSTRUCTION_LIMIT)
        result = runner(text, filename)
    except Exception as exc:
        raise SandboxError(f'{filename}: {exc}') from exc
    data = _to_python(result)
    if not isinstance(data, dict):
        raise SandboxError(f'{filename}: data() did not return a table')
    return data
