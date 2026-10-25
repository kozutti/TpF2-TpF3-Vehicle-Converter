"""Constant evaluation of TPF2's `transf`, `vec2`, `vec3` and `vec4` helpers used inside resource files.

Ported from TPF2 res/scripts/transf.lua, vec3.lua, vec2.lua and vec4.lua. Vectors are {1: x, 2: y, 3: z (, 4: w)}
tables and matrices are {1..16} tables, the shapes the parser already uses for `vec3.new` / `transf.*` results.
"""
import math


def _vec(value, size):
    if not isinstance(value, dict) or any(not isinstance(value.get(i), (int, float)) or isinstance(value.get(i), bool)
                                          for i in range(1, size + 1)):
        raise ValueError(f'vector with {size} components expected')
    return tuple(float(value[i]) for i in range(1, size + 1))


def _number(value):
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValueError('number expected')
    return float(value)


def _matrix_value(value):
    if not isinstance(value, dict) or any(not isinstance(value.get(i), (int, float)) for i in range(1, 17)):
        raise ValueError('4x4 matrix expected')
    return [float(value[i]) for i in range(1, 17)]


def _out(values):
    return {i: v for i, v in enumerate(values, 1)}


def _cols(c0, c1, c2, c3):
    return _out([*c0, *c1, *c2, *c3])


def _mul(m1, m2):
    r = []
    for col in range(4):
        for row in range(4):
            r.append(sum(m1[row + 4 * k] * m2[k + 4 * col] for k in range(4)))
    return r


def new(c0, c1, c2, c3):
    return _cols(_vec(c0, 4), _vec(c1, 4), _vec(c2, 4), _vec(c3, 4))


def flip_y(m):
    m = _matrix_value(m)
    return _out([m[0], -m[1], m[2], m[3], m[4], -m[5], m[6], m[7], m[8], -m[9], m[10], m[11], m[12], -m[13], m[14], m[15]])


def transl(t):
    x, y, z = _vec(t, 3)
    return _cols((1.0, 0.0, 0.0, 0.0), (0.0, 1.0, 0.0, 0.0), (0.0, 0.0, 1.0, 0.0), (x, y, z, 1.0))


def scale(t):
    x, y, z = _vec(t, 3)
    return _cols((x, 0.0, 0.0, 0.0), (0.0, y, 0.0, 0.0), (0.0, 0.0, z, 0.0), (0.0, 0.0, 0.0, 1.0))


def rot_y_cnt_transl(rot_y, cnt, tr):
    rot_y = _number(rot_y)
    cx, cy_, cz = _vec(cnt, 3)
    tx, ty, tz = _vec(tr, 3)
    sy, cy = math.sin(rot_y), math.cos(rot_y)
    return _cols((cy, 0.0, -sy, 0.0), (0.0, 1.0, 0.0, 0.0), (sy, 0.0, cy, 0.0),
                 (-cy * cx - sy * cz + cx + tx, -cy_ + cy_ + ty, sy * cx - cy * cz + cz + tz, 1.0))


def rot_z(angle):
    s, c = math.sin(_number(angle)), math.cos(_number(angle))
    return _cols((c, s, 0.0, 0.0), (-s, c, 0.0, 0.0), (0.0, 0.0, 1.0, 0.0), (0.0, 0.0, 0.0, 1.0))


def rot_y(angle):
    s, c = math.sin(_number(angle)), math.cos(_number(angle))
    return _cols((c, 0.0, -s, 0.0), (0.0, 1.0, 0.0, 0.0), (s, 0.0, c, 0.0), (0.0, 0.0, 0.0, 1.0))


def rot_x(angle):
    s, c = math.sin(_number(angle)), math.cos(_number(angle))
    return _cols((1.0, 0.0, 0.0, 0.0), (0.0, c, s, 0.0), (0.0, -s, c, 0.0), (0.0, 0.0, 0.0, 1.0))


def rot_z_transl(angle, tr):
    s, c = math.sin(_number(angle)), math.cos(_number(angle))
    x, y, z = _vec(tr, 3)
    return _cols((c, s, 0.0, 0.0), (-s, c, 0.0, 0.0), (0.0, 0.0, 1.0, 0.0), (x, y, z, 1.0))


def scale_rot_z_transl(factor, angle, tr):
    factor = _number(factor)
    s, c = math.sin(_number(angle)), math.cos(_number(angle))
    x, y, z = _vec(tr, 3)
    return _cols((factor * c, factor * s, 0.0, 0.0), (factor * -s, factor * c, 0.0, 0.0), (0.0, 0.0, factor, 0.0), (x, y, z, 1.0))


def scale_xyz_rot_z_transl(factors, angle, tr):
    fx, fy, fz = _vec(factors, 3)
    s, c = math.sin(_number(angle)), math.cos(_number(angle))
    x, y, z = _vec(tr, 3)
    return _cols((fx * c, fx * s, 0.0, 0.0), (fy * -s, fy * c, 0.0, 0.0), (0.0, 0.0, fz, 0.0), (x, y, z, 1.0))


def rot_zyx_transl(rot, tr):
    rx, ry, rz = _vec(rot, 3)
    x, y, z = _vec(tr, 3)
    sx, sy, sz = math.sin(rz), math.sin(ry), math.sin(rx)
    cx, cy, cz = math.cos(rz), math.cos(ry), math.cos(rx)
    return _cols((cy * cz, cy * sz, -sy, 0.0),
                 (-cx * sz + sx * sy * cz, cx * cz + sx * sy * sz, sx * cy, 0.0),
                 (sx * sz + cx * sy * cz, -sx * cz + cx * sy * sz, cx * cy, 0.0),
                 (x, y, z, 1.0))


def mul(m1, m2):
    return _out(_mul(_matrix_value(m1), _matrix_value(m2)))


def scale_rot_zyx_transl(factors, rot, tr):
    return mul(rot_zyx_transl(rot, tr), scale(factors))


def deg_to_rad(dx, dy, dz):
    return {1: math.radians(_number(dx)), 2: math.radians(_number(dy)), 3: math.radians(_number(dz))}


def vec4_new(x, y, z, w):
    return {1: _number(x), 2: _number(y), 3: _number(z), 4: _number(w)}


def vec3_add(a, b):
    return _out([x + y for x, y in zip(_vec(a, 3), _vec(b, 3))])


def vec3_sub(a, b):
    return _out([x - y for x, y in zip(_vec(a, 3), _vec(b, 3))])


def vec3_mul(f, v):
    return _out([_number(f) * x for x in _vec(v, 3)])


def vec3_dot(a, b):
    return sum(x * y for x, y in zip(_vec(a, 3), _vec(b, 3)))


def vec3_cross(a, b):
    ax, ay, az = _vec(a, 3)
    bx, by, bz = _vec(b, 3)
    return _out([ay * bz - az * by, az * bx - ax * bz, ax * by - ay * bx])


def vec3_length(v):
    return math.sqrt(sum(x * x for x in _vec(v, 3)))


def vec3_distance(a, b):
    return vec3_length(vec3_sub(a, b))


def vec3_normalize(v):
    length = vec3_length(v)
    return _out([x / length for x in _vec(v, 3)])


def vec2_add(a, b):
    return _out([x + y for x, y in zip(_vec(a, 2), _vec(b, 2))])


def vec2_sub(a, b):
    return _out([x - y for x, y in zip(_vec(a, 2), _vec(b, 2))])


def vec2_mul(f, v):
    return _out([_number(f) * x for x in _vec(v, 2)])


def vec2_length(v):
    return math.hypot(*_vec(v, 2))


def vec2_distance(a, b):
    return vec2_length(vec2_sub(a, b))


# name -> (function, number of arguments)
FUNCTIONS = {
    'transf.new': (new, 4), 'transf.flipY': (flip_y, 1), 'transf.transl': (transl, 1), 'transf.scale': (scale, 1),
    'transf.rotYCntTransl': (rot_y_cnt_transl, 3), 'transf.rotZ': (rot_z, 1), 'transf.rotY': (rot_y, 1), 'transf.rotX': (rot_x, 1),
    'transf.rotZTransl': (rot_z_transl, 2), 'transf.scaleRotZTransl': (scale_rot_z_transl, 3),
    'transf.scaleXYZRotZTransl': (scale_xyz_rot_z_transl, 3), 'transf.rotZYXTransl': (rot_zyx_transl, 2),
    'transf.scaleRotZYXTransl': (scale_rot_zyx_transl, 3), 'transf.degToRad': (deg_to_rad, 3), 'transf.mul': (mul, 2),
    'vec4.new': (vec4_new, 4),
    'vec3.add': (vec3_add, 2), 'vec3.sub': (vec3_sub, 2), 'vec3.mul': (vec3_mul, 2), 'vec3.dot': (vec3_dot, 2),
    'vec3.cross': (vec3_cross, 2), 'vec3.length': (vec3_length, 1), 'vec3.distance': (vec3_distance, 2),
    'vec3.normalize': (vec3_normalize, 1),
    'vec2.add': (vec2_add, 2), 'vec2.sub': (vec2_sub, 2), 'vec2.mul': (vec2_mul, 2), 'vec2.length': (vec2_length, 1),
    'vec2.distance': (vec2_distance, 2),
}
