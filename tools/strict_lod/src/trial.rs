//! Exact `len(blob)` of `lod._pack(definition, positions, faces, quantize_shading=True, shading_step=1e-3, quantize_uv=True)`
//! for the faces produced by one meshoptimizer-fallback trial (see meshopt.py), without building the blob.
//!
//! Static per mesh: corner table (vertex id, attribute values) -> interned pool ids per channel.
//! Per trial: selected corner triples per group -> degenerate filter (same rule as meshopt.py) -> exact size.

use super::{dot3, f32_bits, normal_of, py_round, slice, Map, NONE};

type Key = [u32; 4];

struct Trial {
    pos: Vec<[f64; 3]>,
    corner_vid: Vec<u32>,
    cid: [Vec<u32>; 5],
    entry_bytes: [u64; 5],
    groups: Vec<[u32; 5]>, // corner_start, corner_count, sub, idx_start, idx_count
    orig_idx: Vec<u32>,
    sub_mask: Vec<u32>,
    stamp: [Vec<u32>; 5],
    rank: [Vec<u32>; 5],
    gen: u32,
    streams: Vec<Vec<u32>>,
    out_corners: Vec<[u32; 3]>,
    out_sub: Vec<u32>,
}

static mut TRIAL: *mut Trial = std::ptr::null_mut();

fn shading_key(vals: &[f64], digits: u32) -> Option<Key> {
    let mut v = [0.0f64; 4];
    v[..vals.len()].copy_from_slice(vals);
    if vals.len() >= 3 && vals.iter().all(|x| x.is_finite()) {
        let x = py_round(vals[0], digits)?;
        let y = py_round(vals[1], digits)?;
        let z = py_round(vals[2], digits)?;
        let length = f64::sqrt(dot3([x, y, z], [x, y, z]));
        if length > 1e-12 {
            v[0] = x / length;
            v[1] = y / length;
            v[2] = z / length;
        }
    }
    let mut key = [0u32; 4];
    for m in 0..vals.len() {
        key[m] = f32_bits(v[m]);
    }
    Some(key)
}

fn uv_key(vals: &[f64], digits: Option<u32>) -> Option<Key> {
    let mut key = [0u32; 4];
    for m in 0..vals.len() {
        let v = match digits {
            Some(d) => py_round(vals[m], d)?,
            None => vals[m],
        };
        key[m] = f32_bits(v);
    }
    Some(key)
}

fn intern(map: &mut Map, count: &mut u32, key: Key) -> u32 {
    if let Some(&id) = map.get(&key) {
        return id;
    }
    let id = *count;
    *count += 1;
    map.insert(key, id);
    id
}

unsafe fn init(header_ptr: u32) -> Result<Trial, i32> {
    let h: &[u32] = slice(header_ptr, 18);
    if h[0] != 0x5450_5331 {
        return Err(1);
    }
    let (nv, ncorner, ngroup, nsub) = (h[1] as usize, h[2] as usize, h[3] as usize, h[4] as usize);
    let comps = [h[5] as usize, h[6] as usize, h[7] as usize, h[8] as usize];
    if comps.iter().any(|&c| c > 4) || nsub == 0 || nsub > 4096 {
        return Err(3);
    }
    let stride: usize = comps.iter().sum();
    let pos_raw: &[f64] = slice(h[9], nv * 3);
    let vid_raw: &[u32] = slice(h[10], ncorner);
    let attr_raw: &[f64] = slice(h[11], ncorner * stride);
    let group_raw: &[u32] = slice(h[12], ngroup * 5);
    let idx_raw: &[u32] = slice(h[13], h[14] as usize);
    let mask_raw: &[u32] = slice(h[15], nsub);
    let shading_digits = h[16];
    let uv_digits = if h[17] == u32::MAX { None } else { Some(h[17]) };

    let mut pos = Vec::with_capacity(nv);
    for v in 0..nv {
        let p = [pos_raw[v * 3], pos_raw[v * 3 + 1], pos_raw[v * 3 + 2]];
        if !p.iter().all(|x| x.is_finite()) {
            return Err(2);
        }
        pos.push(p);
    }
    let groups: Vec<[u32; 5]> = group_raw.chunks_exact(5).map(|c| [c[0], c[1], c[2], c[3], c[4]]).collect();
    for g in &groups {
        if (g[0] as usize + g[1] as usize) > ncorner
            || g[2] as usize >= nsub
            || (g[3] as usize + g[4] as usize) > idx_raw.len()
        {
            return Err(1);
        }
    }
    if vid_raw.iter().any(|&v| v as usize >= nv) {
        return Err(1);
    }
    let offs = [0, comps[0], comps[0] + comps[1], comps[0] + comps[1] + comps[2]];
    // sub of each corner: from its group
    let mut corner_sub = vec![0u32; ncorner];
    for g in &groups {
        for c in g[0]..g[0] + g[1] {
            corner_sub[c as usize] = g[2];
        }
    }
    let mut maps: [Map; 5] = Default::default();
    let mut counts = [0u32; 5];
    let mut cid: [Vec<u32>; 5] = [
        vec![NONE; ncorner],
        vec![NONE; ncorner],
        vec![NONE; ncorner],
        vec![NONE; ncorner],
        vec![NONE; ncorner],
    ];
    for c in 0..ncorner {
        let mask = mask_raw[corner_sub[c] as usize];
        let p = pos[vid_raw[c] as usize];
        cid[0][c] = intern(&mut maps[0], &mut counts[0], [f32_bits(p[0]), f32_bits(p[1]), f32_bits(p[2]), 0]);
        for ch in 1..5usize {
            if mask >> ch & 1 == 0 {
                continue;
            }
            let k = comps[ch - 1];
            let at = c * stride + offs[ch - 1];
            let vals = &attr_raw[at..at + k];
            let key = if ch <= 2 {
                shading_key(vals, shading_digits).ok_or(3)?
            } else {
                uv_key(vals, uv_digits).ok_or(3)?
            };
            cid[ch][c] = intern(&mut maps[ch], &mut counts[ch], key);
        }
    }
    Ok(Trial {
        pos,
        corner_vid: vid_raw.to_vec(),
        cid,
        entry_bytes: [12, comps[0] as u64 * 4, comps[1] as u64 * 4, comps[2] as u64 * 4, comps[3] as u64 * 4],
        groups,
        orig_idx: idx_raw.to_vec(),
        sub_mask: mask_raw.to_vec(),
        stamp: [
            vec![0; counts[0] as usize],
            vec![0; counts[1] as usize],
            vec![0; counts[2] as usize],
            vec![0; counts[3] as usize],
            vec![0; counts[4] as usize],
        ],
        rank: [
            vec![0; counts[0] as usize],
            vec![0; counts[1] as usize],
            vec![0; counts[2] as usize],
            vec![0; counts[3] as usize],
            vec![0; counts[4] as usize],
        ],
        gen: 0,
        streams: vec![Vec::new(); nsub * 5],
        out_corners: Vec::new(),
        out_sub: Vec::new(),
    })
}

impl Trial {
    fn size(&mut self, selected: &[u32], lens: &[u32]) -> i64 {
        self.out_corners.clear();
        self.out_sub.clear();
        let mut off = 0usize;
        for (gi, g) in self.groups.iter().enumerate() {
            let [cs, cc, sub, is, ic] = *g;
            let len = lens[gi] as usize;
            if off + len > selected.len() || len % 3 != 0 {
                return -2;
            }
            let sel = &selected[off..off + len];
            off += len;
            let start = self.out_corners.len();
            for tri in sel.chunks_exact(3) {
                if tri.iter().any(|&t| t >= cc) {
                    return -2;
                }
                let c = [cs + tri[0], cs + tri[1], cs + tri[2]];
                let ids = [
                    self.corner_vid[c[0] as usize],
                    self.corner_vid[c[1] as usize],
                    self.corner_vid[c[2] as usize],
                ];
                if ids[0] == ids[1] || ids[1] == ids[2] || ids[0] == ids[2] {
                    continue;
                }
                let p = [self.pos[ids[0] as usize], self.pos[ids[1] as usize], self.pos[ids[2] as usize]];
                if normal_of(p).is_none() {
                    continue;
                }
                self.out_corners.push(c);
                self.out_sub.push(sub);
            }
            if self.out_corners.len() == start {
                // every triangle of the group vanished: the source component is kept as it is
                for tri in self.orig_idx[is as usize..(is + ic) as usize].chunks_exact(3) {
                    self.out_corners.push([cs + tri[0], cs + tri[1], cs + tri[2]]);
                    self.out_sub.push(sub);
                }
            }
        }
        self.gen = self.gen.wrapping_add(1);
        if self.gen == 0 {
            for ch in 0..5 {
                self.stamp[ch].iter_mut().for_each(|s| *s = 0);
            }
            self.gen = 1;
        }
        let gen = self.gen;
        for s in self.streams.iter_mut() {
            s.clear();
        }
        let mut next = [0u32; 5];
        for (f, corners) in self.out_corners.iter().enumerate() {
            let s = self.out_sub[f] as usize;
            let mask = self.sub_mask[s];
            for &c in corners {
                for ch in 0..5usize {
                    if mask >> ch & 1 == 1 {
                        let id = self.cid[ch][c as usize] as usize;
                        if self.stamp[ch][id] != gen {
                            self.stamp[ch][id] = gen;
                            self.rank[ch][id] = next[ch];
                            next[ch] += 1;
                        }
                        self.streams[s * 5 + ch].push(self.rank[ch][id]);
                    }
                }
            }
        }
        let mut total = 0u64;
        for ch in 0..5 {
            total += next[ch] as u64 * self.entry_bytes[ch];
        }
        let mut unique: Vec<usize> = Vec::new();
        for i in 0..self.streams.len() {
            if self.streams[i].is_empty() {
                continue;
            }
            if unique.iter().any(|&j| self.streams[j] == self.streams[i]) {
                continue;
            }
            unique.push(i);
            total += 4 * self.streams[i].len() as u64;
        }
        total as i64
    }
}

#[no_mangle]
pub extern "C" fn tp_init(header_ptr: u32) -> i32 {
    unsafe {
        match init(header_ptr) {
            Ok(t) => {
                TRIAL = Box::into_raw(Box::new(t));
                0
            }
            Err(code) => code,
        }
    }
}

/// selected: u32 corner indices (local to each group) concatenated over groups; lens: u32 length per group.
#[no_mangle]
pub extern "C" fn tp_size(sel_ptr: u32, total: u32, lens_ptr: u32) -> i64 {
    unsafe {
        if TRIAL.is_null() {
            return -1;
        }
        let t = &mut *TRIAL;
        let selected: &[u32] = slice(sel_ptr, total as usize);
        let lens: &[u32] = slice(lens_ptr, t.groups.len());
        t.size(selected, lens)
    }
}
