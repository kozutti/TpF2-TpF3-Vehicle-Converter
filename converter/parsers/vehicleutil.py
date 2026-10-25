"""Constant evaluation of TPF2's `vehicleutil` animation helpers (steam locomotive rods and pistons).

TPF2 mesh files may call `vehicleutil.makePistonAnim(vec2.new(..), vec2.new(..), vec2.new(..))` while they are
loaded. TPF3 ships no `vehicleutil`, so the converter evaluates the helpers to the plain keyframe tables they return.
Ported from TPF2 res/scripts/vehicleutil.lua; vec2 values are {1: x, 2: y} tables here.
"""
import math

STEPS = 16  # calcNumSteps() in TPF2 is a constant


def _duration(wheel_center):
    return 1000 * (2.0 * math.pi * wheel_center[2])  # distToTime(2 pi r)


def _matrix(*rows):
    return {i: value for i, value in enumerate((v for row in rows for v in row), 1)}


def _translation(x, y, z):
    return _matrix((1.0, 0.0, 0.0, 0.0), (0.0, 1.0, 0.0, 0.0), (0.0, 0.0, 1.0, 0.0), (x, y, z, 1.0))


def _rot_y_cnt_transl(rot_y, cnt, transl):
    sy, cy = math.sin(rot_y), math.cos(rot_y)
    tx, ty, tz = cnt[0] + transl[0], cnt[1] + transl[1], cnt[2] + transl[2]
    return _matrix((cy, 0.0, -sy, 0.0), (0.0, 1.0, 0.0, 0.0), (sy, 0.0, cy, 0.0),
                   (-cy * cnt[0] - sy * cnt[2] + tx, -cnt[1] + ty, sy * cnt[0] - cy * cnt[2] + tz, 1.0))


def _xy(vector):
    if not isinstance(vector, dict) or 1 not in vector or 2 not in vector:
        raise ValueError('vec2 expected')
    return float(vector[1]), float(vector[2])


def _result(keyframes):
    return {'type': 'KEYFRAME_MATRIX', 'params': {'keyframes': {i: frame for i, frame in enumerate(keyframes, 1)}}}


def _wheel_positions(wheel_center, wheel_pos0):
    cx, cy = _xy(wheel_center)
    px, py = _xy(wheel_pos0)
    radius = math.hypot(px - cx, py - cy)
    angle0 = math.atan2(py - cy, cx - px)
    for i in range(STEPS + 1):
        f = i / STEPS
        angle = angle0 + 2.0 * math.pi * f
        yield f, (cx + radius * -math.cos(angle), cy + radius * math.sin(angle))


def make_coupling_rod_anim(wheel_center, wheel_pos0):
    duration = _duration((0, 0, _xy(wheel_center)[1]))
    x0, y0 = _xy(wheel_pos0)
    return _result({'time': duration * f, 'transf': _translation(x - x0, 0.0, y - y0)}
                   for f, (x, y) in _wheel_positions(wheel_center, wheel_pos0))


def _piston(wheel_center, wheel_pos0, piston_pos0):
    wx0, wy0 = _xy(wheel_pos0)
    px0, py0 = _xy(piston_pos0)
    length = math.hypot(wx0 - px0, wy0 - py0)
    rod_angle0 = math.atan((wy0 - py0) / (px0 - wx0))
    duration = _duration((0, 0, _xy(wheel_center)[1]))
    for f, (x, y) in _wheel_positions(wheel_center, wheel_pos0):
        dy = y - py0
        dx = math.sqrt(length * length - dy * dy)
        yield f, duration, x + dx, dy, dx, rod_angle0, (px0, py0), (wx0, wy0)


def make_piston_anim(wheel_center, wheel_pos0, piston_pos0):
    return _result({'time': duration * f, 'transf': _translation(piston_x - piston0[0], 0.0, 0.0)}
                   for f, duration, piston_x, dy, dx, rod0, piston0, wheel0 in _piston(wheel_center, wheel_pos0, piston_pos0))


def make_connecting_rod_anim(wheel_center, wheel_pos0, piston_pos0):
    return _result({'time': duration * f,
                    'transf': _rot_y_cnt_transl(math.atan(dy / dx) - rod0,
                                                (piston0[0] - wheel0[0], 0.0, piston0[1] - wheel0[1]),
                                                (piston_x - piston0[0], 0.0, 0.0))}
                   for f, duration, piston_x, dy, dx, rod0, piston0, wheel0 in _piston(wheel_center, wheel_pos0, piston_pos0))


FUNCTIONS = {'vehicleutil.makeCouplingRodAnim': (make_coupling_rod_anim, 2),
             'vehicleutil.makePistonAnim': (make_piston_anim, 3),
             'vehicleutil.makeConnectingRodAnim': (make_connecting_rod_anim, 3)}
