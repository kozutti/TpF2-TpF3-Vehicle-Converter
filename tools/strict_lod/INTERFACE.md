# strict_lod WASM: ABI (contract between Rust core and Python wrapper)

Purpose: a byte-exact native port of `converter/transforms/lod.py::simplify_mesh` (the strict edge-contraction
LOD simplifier). Python keeps `_decode` / `_pack` / orchestration; Rust runs everything in between
(topology, candidate checks, heap, collapses, and the exact "blob size after _pack" budget checks).

Module file: `converter/vendor/strict_lod.wasm`. No imports allowed (same policy as meshoptimizer wasm).
Use ONE FRESH INSTANCE PER mesh (no free/reset API; `sl_alloc` leaks by design).

## Exports

| name | signature | meaning |
|---|---|---|
| `memory` | linear memory | |
| `sl_alloc` | `(size: i32) -> i32` | returns pointer to `size` zeroed bytes, 8-byte aligned |
| `sl_init` | `(header_ptr: i32) -> i32` | parse header + arrays, build state. 0 = ok, otherwise error code (see below) |
| `sl_step` | `(max_units: i32) -> i32` | do up to `max_units` units of work (one unit = one vertex enqueue or one heap pop). Returns 0 = call again, 1 = finished EARLY (budget met at a 60-collapse checkpoint), 2 = finished FINAL (all stages done / work limit), negative = error |
| `sl_out_faces` | `() -> i32` | ptr to `u32[nf*3]`: final vertex ids per face corner. Deleted face: all three = 0xFFFFFFFF |
| `sl_out_uv` | `() -> i32` | ptr to `f64[nf*3*uv_stride]`: final uv values per corner (uv0 comps then uv1 comps; `uv_stride = comps_uv0 + comps_uv1`; layout `((face*3+corner)*uv_stride + k)`). Only meaningful for alive faces and for channels present in that face's sub |
| `sl_out_stats` | `() -> i32` | ptr to `f64[8]`: `[collapses, max_plane_error, max_uv_change, work_used, components_after, work_limit_reached (0/1), 0, 0]` |

`sl_init` error codes: 1 header/array invalid, 2 non-finite position or uv input (caller must fall back to the
pure Python `simplify_mesh`), 3 unsupported value (component count > 4, value too large for exact rounding),
4 internal. Any non-zero code, trap, or missing wasm => caller falls back to the pure-Python path
(result must still be correct, only slow). `sl_step` negative => same fallback.

## Header (array of little-endian u32 words, written to memory from `sl_alloc`)

| idx | meaning |
|---|---|
| 0 | magic `0x4C545331` |
| 1 | `nv` number of vertices (positions) |
| 2 | `nf` number of faces (as returned by `lod._decode`; zero-area faces already dropped) |
| 3 | `nsub` number of sub meshes (`len(_array(definition['subMeshes']))`) |
| 4 | `has_max_work` (0/1) |
| 5,6 | `max_work` lo, hi (u64) |
| 7,8 | `budget` lo, hi (u64) = `budget` argument (bytes, compared with packed blob length) |
| 9 | `comps_normal` (numComp of vertexAttr 'normal', 0 if the attribute does not exist) |
| 10 | `comps_tangent` (0 if absent) |
| 11 | `comps_uv0` (0 if absent) |
| 12 | `comps_uv1` (0 if absent) |
| 13 | ptr `positions`: `f64[nv*3]` |
| 14 | ptr `face_ids`: `u32[nf*3]` |
| 15 | ptr `face_sub`: `u32[nf]` |
| 16 | ptr `sub_mask`: `u32[nsub]`, bit0 = position (always set), bit1 normal, bit2 tangent, bit3 uv0, bit4 uv1 — set iff that name is a key of `sub['indices']` (i.e. a stream of that sub; equals the keys of every face's per-corner `attrs` dict for faces of that sub) |
| 17 | ptr `corner_attr`: `f64[nf*3*stride]`, `stride = comps_normal+comps_tangent+comps_uv0+comps_uv1`; per corner values in channel order normal, tangent, uv0, uv1 (absent channel/sub-without-stream: zeros) |

Values are the Python floats from `_decode` (f32-origin doubles). Index `((face*3+corner)*stride + channel_offset + k)`.

## Python wrapper contract (`converter/transforms/strict_lod.py`)

`simplify_mesh_native(definition, blob, budget=lod.TARGET_BYTES, cancel=None, max_work=None)` must return exactly what
`lod.simplify_mesh(...)` returns — same tuple `(definition, blob_bytes, stats)` with an identical `stats` dict
(same keys, same values, same early-return vs final-path key sets) — or fall back to `lod.simplify_mesh`
(same args) when native is unavailable/unsupported. Flow:

1. `positions, faces = lod._decode(definition, blob)`; first `lod._pack(..., quantize_shading=True)` budget check and its
   unchanged early return are done in Python exactly like `simplify_mesh`.
2. Build header/arrays, `sl_init`, then loop `sl_step(N)` (N ~ 2000) checking `cancel` between calls
   (`lod._cancel`; raise `InterruptedError('最低LODの生成を取り消しました。')` like the original) until status 1/2.
3. Rebuild `faces` (None for deleted; ids from `sl_out_faces`; uv tuples from `sl_out_uv` replacing the uv attrs)
   and call `lod._pack(definition, positions, faces, quantize_shading=True)` once for the returned definition/blob/stats.
4. Stats: status 1 (early) => the "early return" stats update from `simplify_mesh` (no contraction_work keys,
   budget_met=True); status 2 => the final stats update including `contraction_work*` keys and
   `budget_met = stats['budget_bytes'] <= budget`. `components_before` = number of connected (material,topology)
   components computed by the wrapper exactly as `simplify_mesh` does (or ask Rust; wrapper may compute in Python
   cheaply with the same union-find). `components_after` comes from `sl_out_stats[4]`.
5. Everything not speed-critical stays in Python.

## Trial-size evaluator (meshopt fallback), `src/trial.rs`

Same wasm module, separate global state (`tp_*` exports). Used by `converter/transforms/meshopt_fast.py`.

| name | signature | meaning |
|---|---|---|
| `tp_init` | `(header_ptr: i32) -> i32` | 0 ok; 1 invalid arrays, 2 non-finite position, 3 unsupported value |
| `tp_size` | `(sel_ptr: i32, total: i32, lens_ptr: i32) -> i64` | exact `len(blob)` of `lod._pack(..., quantize_shading=True, shading_step=1e-3, quantize_uv=True)` for the faces built from the selected corner triples; negative = error |

Header words (u32): 0 magic `0x54505331`; 1 nv; 2 ncorner; 3 ngroup; 4 nsub; 5-8 comps normal/tangent/uv0/uv1;
9 ptr positions `f64[nv*3]`; 10 ptr corner_vid `u32[ncorner]`; 11 ptr corner_attr `f64[ncorner*stride]`;
12 ptr group_info `u32[ngroup*5]` = (corner_start, corner_count, sub, idx_start, idx_count); 13 ptr orig_idx `u32[]`;
14 orig_idx length; 15 ptr sub_mask `u32[nsub]`; 16 shading round digits (3); 17 uv round digits (5, or `0xFFFFFFFF` = none).

`tp_size`: `selected` = simplifier output (corner indices local to each group) concatenated over groups, `lens` = u32 count per group.
Triangles with repeated vertex ids or a degenerate normal are dropped (meshopt.py rule); a group that loses every
triangle is replaced by its original faces (`orig_idx`). The Python caller rebuilds the chosen trial with the reference
code and verifies that the real packed size equals this prediction (else it falls back to the reference).

Other exports: `sl_dbg_round(x: f64, nd: i32) -> f64` (CPython-exact `round`, NaN if unsupported) for tests.
