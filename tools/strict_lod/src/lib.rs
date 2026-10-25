//! Byte-exact native port of `converter/transforms/lod.py::simplify_mesh` (strict edge contraction).
//! ABI: see `../INTERFACE.md`. All float arithmetic mirrors the Python operation order (f64, no fused ops).

use std::cmp::{Ordering, Reverse};
use std::collections::{BinaryHeap, HashMap};
use std::hash::{BuildHasherDefault, Hasher};

mod trial;

const COS8: f64 = 0.9902680687415704; // math.cos(math.radians(8))
const NONE: u32 = u32::MAX;
const LIMITS: [f64; 4] = [0.00001, 0.002, 0.01, 0.03];

type Key = [u32; 4];

#[derive(Default)]
struct Fx(u64);
impl Fx {
    #[inline]
    fn add(&mut self, v: u64) {
        self.0 = (self.0.rotate_left(5) ^ v).wrapping_mul(0x517c_c1b7_2722_0a95);
    }
}
impl Hasher for Fx {
    fn write(&mut self, bytes: &[u8]) {
        for chunk in bytes.chunks(8) {
            let mut v = 0u64;
            for (i, b) in chunk.iter().enumerate() {
                v |= (*b as u64) << (8 * i);
            }
            self.add(v);
        }
    }
    fn write_u32(&mut self, i: u32) {
        self.add(i as u64);
    }
    fn write_usize(&mut self, i: usize) {
        self.add(i as u64);
    }
    fn finish(&self) -> u64 {
        self.0
    }
}
type Map = HashMap<Key, u32, BuildHasherDefault<Fx>>;

// ---------------------------------------------------------------- Python-exact helpers

/// `round(x, nd)` of CPython (correct rounding of the exact binary value, ties to even).
/// None if the result cannot be produced exactly (caller falls back to Python).
fn py_round(x: f64, nd: u32) -> Option<f64> {
    if !x.is_finite() || x == 0.0 {
        return Some(x);
    }
    let bits = x.to_bits();
    let neg = bits >> 63 != 0;
    let exp = ((bits >> 52) & 0x7ff) as i32;
    let frac = bits & ((1u64 << 52) - 1);
    let (m, e) = if exp == 0 {
        (frac as u128, -1074i32)
    } else {
        (((frac | (1u64 << 52)) as u128), exp - 1075)
    };
    if e >= 0 {
        return Some(x);
    }
    let p10 = 10u128.pow(nd);
    let num = m * p10;
    let s = (-e) as u32;
    let k: u128 = if s >= 128 {
        0
    } else {
        let q = num >> s;
        let r = num & ((1u128 << s) - 1);
        let half = 1u128 << (s - 1);
        if r > half || (r == half && (q & 1) == 1) {
            q + 1
        } else {
            q
        }
    };
    if k > (1u128 << 53) {
        return None;
    }
    let mag = (k as f64) / (p10 as f64);
    Some(if neg { -mag } else { mag })
}

/// f64 -> f32 bit pattern as `struct.pack('<f', v)` does on x86 (NaN payload kept, quieted).
fn f32_bits(v: f64) -> u32 {
    if v.is_nan() {
        let b = v.to_bits();
        let sign = (b >> 63) as u32;
        let frac = ((b & ((1u64 << 52) - 1)) >> 29) as u32;
        return (sign << 31) | 0x7f80_0000 | 0x0040_0000 | (frac & 0x003f_ffff);
    }
    (v as f32).to_bits()
}

/// `sum(x*y for x, y in zip(a, b))` of CPython >= 3.12: first item added to int 0, then Neumaier compensated summation.
#[inline]
fn dot3(a: [f64; 3], b: [f64; 3]) -> f64 {
    let mut f = 0.0 + a[0] * b[0];
    let mut c = 0.0f64;
    for x in [a[1] * b[1], a[2] * b[2]] {
        let t = f + x;
        if f.abs() >= x.abs() {
            c += (f - t) + x;
        } else {
            c += (x - t) + f;
        }
        f = t;
    }
    if c != 0.0 && c.is_finite() {
        f += c;
    }
    f
}
#[inline]
fn sub3(a: [f64; 3], b: [f64; 3]) -> [f64; 3] {
    [a[0] - b[0], a[1] - b[1], a[2] - b[2]]
}
#[inline]
fn cross3(a: [f64; 3], b: [f64; 3]) -> [f64; 3] {
    [
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    ]
}
fn normal_of(p: [[f64; 3]; 3]) -> Option<[f64; 3]> {
    let n = cross3(sub3(p[1], p[0]), sub3(p[2], p[0]));
    let length = dot3(n, n).sqrt();
    if length > 1e-14 {
        Some([n[0] / length, n[1] / length, n[2] / length])
    } else {
        None
    }
}

fn inter(a: &[u32], b: &[u32]) -> Vec<u32> {
    let (mut i, mut j) = (0, 0);
    let mut out = Vec::new();
    while i < a.len() && j < b.len() {
        match a[i].cmp(&b[j]) {
            Ordering::Less => i += 1,
            Ordering::Greater => j += 1,
            Ordering::Equal => {
                out.push(a[i]);
                i += 1;
                j += 1;
            }
        }
    }
    out
}
fn inter_count(a: &[u32], b: &[u32]) -> usize {
    let (mut i, mut j, mut n) = (0, 0, 0);
    while i < a.len() && j < b.len() {
        match a[i].cmp(&b[j]) {
            Ordering::Less => i += 1,
            Ordering::Greater => j += 1,
            Ordering::Equal => {
                n += 1;
                i += 1;
                j += 1;
            }
        }
    }
    n
}
fn union(a: &[u32], b: &[u32]) -> Vec<u32> {
    let mut v: Vec<u32> = a.iter().chain(b.iter()).copied().collect();
    v.sort_unstable();
    v.dedup();
    v
}
fn set_insert(v: &mut Vec<u32>, x: u32) {
    if let Err(p) = v.binary_search(&x) {
        v.insert(p, x);
    }
}
fn set_remove(v: &mut Vec<u32>, x: u32) {
    if let Ok(p) = v.binary_search(&x) {
        v.remove(p);
    }
}
#[inline]
fn zero_norm(x: f64) -> f64 {
    x + 0.0
}

#[derive(Clone, Copy)]
struct Item {
    cost: f64,
    src: u32,
    dst: u32,
    vs: u32,
    vd: u32,
}
impl PartialEq for Item {
    fn eq(&self, o: &Self) -> bool {
        self.cmp(o) == Ordering::Equal
    }
}
impl Eq for Item {}
impl PartialOrd for Item {
    fn partial_cmp(&self, o: &Self) -> Option<Ordering> {
        Some(self.cmp(o))
    }
}
impl Ord for Item {
    fn cmp(&self, o: &Self) -> Ordering {
        self.cost
            .total_cmp(&o.cost)
            .then(self.src.cmp(&o.src))
            .then(self.dst.cmp(&o.dst))
            .then(self.vs.cmp(&o.vs))
            .then(self.vd.cmp(&o.vd))
    }
}

// ---------------------------------------------------------------- state

struct State {
    nv: usize,
    nf: usize,
    nsub: usize,
    pos: Vec<[f64; 3]>,
    ids: Vec<[u32; 3]>,
    sub: Vec<u32>,
    alive: Vec<bool>,
    fnormal: Vec<[f64; 3]>,
    comps: [usize; 4], // normal, tangent, uv0, uv1
    uvk: [usize; 2],
    uv: [Vec<f64>; 2],
    sub_mask: Vec<u32>,
    incident: Vec<Vec<u32>>,
    planes: Vec<Vec<[f64; 4]>>,
    comp_id: Vec<u32>,
    comp_count: Vec<i64>,
    components_before: usize,
    versions: Vec<u32>,
    heap: BinaryHeap<Reverse<Item>>,
    work_used: u64,
    max_work: Option<u64>,
    budget: u64,
    collapses: u64,
    since_pack: u32,
    max_error: f64,
    max_uv: f64,
    stage: usize,
    phase: u8,
    cursor: usize,
    // exact blob-size tracking (mirror of lod._pack)
    interner: [Map; 5],
    refcnt: [Vec<u32>; 5],
    distinct: [u64; 5],
    cid: [Vec<u32>; 5],
    entry_bytes: [u64; 5],
    rank: [Vec<u32>; 5],
    streams: Vec<Vec<u32>>,
    // outputs
    out_ids: Vec<u32>,
    out_uv: Vec<f64>,
    out_stats: [f64; 8],
}

static mut STATE: *mut State = std::ptr::null_mut();

impl State {
    fn limit_hit(&self) -> bool {
        matches!(self.max_work, Some(m) if self.work_used >= m)
    }

    fn neighbors(&self, v: u32) -> Vec<u32> {
        let mut out = Vec::new();
        for &f in &self.incident[v as usize] {
            for &w in &self.ids[f as usize] {
                if w != v {
                    out.push(w);
                }
            }
        }
        out.sort_unstable();
        out.dedup();
        out
    }

    fn uv_gradient(&self, f: usize, c: usize) -> Option<[[f64; 3]; 4]> {
        let id = self.ids[f];
        let p = [
            self.pos[id[0] as usize],
            self.pos[id[1] as usize],
            self.pos[id[2] as usize],
        ];
        let e1 = sub3(p[1], p[0]);
        let e2 = sub3(p[2], p[0]);
        let aa = dot3(e1, e1);
        let ab = dot3(e1, e2);
        let bb = dot3(e2, e2);
        let det = aa * bb - ab * ab;
        if det <= 1e-20 {
            return None;
        }
        let k = self.uvk[c];
        let base = f * 3 * k;
        let uv = &self.uv[c];
        let mut g = [[0.0f64; 3]; 4];
        for m in 0..k {
            let du = uv[base + k + m] - uv[base + m];
            let dv = uv[base + 2 * k + m] - uv[base + m];
            let a = (du * bb - dv * ab) / det;
            let b = (dv * aa - du * ab) / det;
            for i in 0..3 {
                g[m][i] = a * e1[i] + b * e2[i];
            }
        }
        Some(g)
    }

    fn candidate(&mut self, src: u32, dst: u32, error_limit: f64) -> Option<f64> {
        let (s, d) = (src as usize, dst as usize);
        self.work_used +=
            1 + self.incident[s].len() as u64 + self.incident[d].len() as u64 + self.planes[s].len() as u64;
        if self.limit_hit() {
            return None;
        }
        if self.incident[s].is_empty() || self.incident[d].is_empty() {
            return None;
        }
        let shared = inter(&self.incident[s], &self.incident[d]);
        if !(1..=2).contains(&shared.len()) {
            return None;
        }
        {
            let mut removed: Vec<(u32, i64)> = Vec::new();
            for &f in &shared {
                let c = self.comp_id[f as usize];
                match removed.iter_mut().find(|e| e.0 == c) {
                    Some(e) => e.1 += 1,
                    None => removed.push((c, 1)),
                }
            }
            if removed.iter().any(|&(c, r)| self.comp_count[c as usize] <= r) {
                return None;
            }
        }
        let n_src = self.neighbors(src);
        let n_dst = self.neighbors(dst);
        let mut opposite: Vec<u32> = Vec::new();
        for &f in &shared {
            for &v in &self.ids[f as usize] {
                if v != src && v != dst {
                    opposite.push(v);
                }
            }
        }
        opposite.sort_unstable();
        opposite.dedup();
        if inter(&n_src, &n_dst) != opposite {
            return None; // link condition
        }
        let first_sub = self.sub[self.incident[s][0] as usize];
        if self.incident[s]
            .iter()
            .chain(self.incident[d].iter())
            .any(|&f| self.sub[f as usize] != first_sub)
        {
            return None;
        }
        let mut boundary: Vec<u32> = Vec::new();
        for &v in &n_src {
            if inter_count(&self.incident[s], &self.incident[v as usize]) == 1 {
                boundary.push(v);
            }
        }
        if !boundary.is_empty() {
            if boundary.len() != 2 || !boundary.contains(&dst) {
                return None;
            }
            let a = self.pos[boundary[0] as usize];
            let b = self.pos[boundary[1] as usize];
            let segment = sub3(b, a);
            let length2 = dot3(segment, segment);
            if length2 == 0.0 {
                return None;
            }
            let t = dot3(sub3(self.pos[s], a), segment) / length2;
            let projected = [a[0] + t * segment[0], a[1] + t * segment[1], a[2] + t * segment[2]];
            let off = sub3(self.pos[s], projected);
            if !(t >= 0.0 && t <= 1.0) || dot3(off, off).sqrt() > error_limit.min(0.002) {
                return None;
            }
        }
        let mut error = 0.0f64;
        for plane in &self.planes[s] {
            let deviation = (dot3([plane[0], plane[1], plane[2]], self.pos[d]) - plane[3]).abs();
            if deviation > error_limit {
                return None;
            }
            if deviation > error {
                error = deviation;
            }
        }
        let mask = self.sub_mask[first_sub as usize];
        let delta = sub3(self.pos[d], self.pos[s]);
        for c in 0..2usize {
            if mask >> (3 + c) & 1 == 0 {
                continue;
            }
            let k = self.uvk[c];
            // groups: island key -> projected uvs
            let mut groups: Vec<([f64; 4], Vec<[f64; 4]>)> = Vec::new();
            let mut edge_uv: Vec<([f64; 4], [f64; 4])> = Vec::new();
            for &fid in &self.incident[s] {
                let f = fid as usize;
                let corner = self.ids[f].iter().position(|&v| v == src).unwrap();
                let gradient = match self.uv_gradient(f, c) {
                    Some(g) => g,
                    None => return None,
                };
                let base = (f * 3 + corner) * k;
                let mut key = [0.0f64; 4];
                let mut projected = [0.0f64; 4];
                for m in 0..k {
                    let value = self.uv[c][base + m];
                    projected[m] = value + dot3(gradient[m], delta);
                    key[m] = zero_norm(py_round(value, 5)?);
                }
                match groups.iter_mut().find(|g| g.0 == key) {
                    Some(g) => g.1.push(projected),
                    None => groups.push((key, vec![projected])),
                }
                if let Some(dcorner) = self.ids[f].iter().position(|&v| v == dst) {
                    let dbase = (f * 3 + dcorner) * k;
                    let mut dv = [0.0f64; 4];
                    for m in 0..k {
                        dv[m] = self.uv[c][dbase + m];
                    }
                    edge_uv.push((key, dv));
                }
            }
            for (key, projected_values) in &groups {
                let endpoints: Vec<[f64; 4]> = edge_uv.iter().filter(|e| e.0 == *key).map(|e| e.1).collect();
                if endpoints.is_empty() {
                    return None;
                }
                let expected = endpoints[0];
                for uv in projected_values.iter().chain(endpoints.iter()) {
                    for m in 0..k {
                        if (expected[m] - uv[m]).abs() > 0.0002 {
                            return None;
                        }
                    }
                }
            }
        }
        for &fid in &self.incident[s] {
            if shared.binary_search(&fid).is_ok() {
                continue;
            }
            let id = self.ids[fid as usize];
            let pt = |v: u32| self.pos[if v == src { d } else { v as usize }];
            let normal = normal_of([pt(id[0]), pt(id[1]), pt(id[2])])?;
            if dot3(normal, self.fnormal[fid as usize]) < COS8 {
                return None;
            }
        }
        Some(error)
    }

    fn enqueue(&mut self, src: u32, limit: f64) {
        if self.incident[src as usize].is_empty() {
            return;
        }
        for dst in self.neighbors(src) {
            if let Some(error) = self.candidate(src, dst, limit) {
                let d = sub3(self.pos[src as usize], self.pos[dst as usize]);
                let length2 = dot3(d, d);
                self.heap.push(Reverse(Item {
                    cost: error * error + length2 * 1e-9,
                    src,
                    dst,
                    vs: self.versions[src as usize],
                    vd: self.versions[dst as usize],
                }));
            }
        }
    }

    // ---- pool bookkeeping
    fn pool_inc(&mut self, ch: usize, id: u32) {
        let r = &mut self.refcnt[ch][id as usize];
        if *r == 0 {
            self.distinct[ch] += 1;
        }
        *r += 1;
    }
    fn pool_dec(&mut self, ch: usize, id: u32) {
        let r = &mut self.refcnt[ch][id as usize];
        *r -= 1;
        if *r == 0 {
            self.distinct[ch] -= 1;
        }
    }
    fn intern(&mut self, ch: usize, key: Key) -> u32 {
        if let Some(&id) = self.interner[ch].get(&key) {
            return id;
        }
        let id = self.refcnt[ch].len() as u32;
        self.refcnt[ch].push(0);
        self.interner[ch].insert(key, id);
        id
    }
    fn uv_key(&self, c: usize, f: usize, corner: usize) -> Key {
        let k = self.uvk[c];
        let base = (f * 3 + corner) * k;
        let mut key = [0u32; 4];
        for m in 0..k {
            key[m] = f32_bits(self.uv[c][base + m]);
        }
        key
    }

    /// Exact `len(blob)` of `lod._pack(..., quantize_shading=True)` for the current faces.
    fn exact_size(&mut self) -> u64 {
        let mut total = 0u64;
        for ch in 0..5 {
            total += self.distinct[ch] * self.entry_bytes[ch];
        }
        for ch in 0..5 {
            let n = self.refcnt[ch].len();
            self.rank[ch].clear();
            self.rank[ch].resize(n, NONE);
        }
        let mut streams = std::mem::take(&mut self.streams);
        for s in streams.iter_mut() {
            s.clear();
        }
        let mut next = [0u32; 5];
        for f in 0..self.nf {
            if !self.alive[f] {
                continue;
            }
            let s = self.sub[f] as usize;
            let mask = self.sub_mask[s];
            for j in 0..3 {
                for ch in 0..5 {
                    if mask >> ch & 1 == 1 {
                        let id = self.cid[ch][f * 3 + j] as usize;
                        let r = &mut self.rank[ch][id];
                        if *r == NONE {
                            *r = next[ch];
                            next[ch] += 1;
                        }
                        streams[s * 5 + ch].push(*r);
                    }
                }
            }
        }
        let mut unique: Vec<usize> = Vec::new();
        for i in 0..streams.len() {
            if streams[i].is_empty() {
                continue;
            }
            if unique.iter().any(|&j| streams[j] == streams[i]) {
                continue;
            }
            unique.push(i);
            total += 4 * streams[i].len() as u64;
        }
        self.streams = streams;
        total
    }

    fn collapse(&mut self, src: u32, dst: u32, error: f64) -> Option<Vec<u32>> {
        let (s, d) = (src as usize, dst as usize);
        let changed = union(&self.incident[s], &self.incident[d]);
        let mut touched: Vec<u32> = Vec::new();
        for &f in &changed {
            touched.extend_from_slice(&self.ids[f as usize]);
        }
        touched.sort_unstable();
        touched.dedup();
        let shared = inter(&self.incident[s], &self.incident[d]);
        let delta = sub3(self.pos[d], self.pos[s]);
        let src_faces = self.incident[s].clone();
        for &fid in &src_faces {
            let f = fid as usize;
            if shared.binary_search(&fid).is_ok() {
                let id = self.ids[f];
                for &v in &id {
                    set_remove(&mut self.incident[v as usize], fid);
                }
                self.comp_count[self.comp_id[f] as usize] -= 1;
                // drop pool references
                let mask = self.sub_mask[self.sub[f] as usize];
                for j in 0..3 {
                    for ch in 0..5 {
                        if mask >> ch & 1 == 1 {
                            let cid = self.cid[ch][f * 3 + j];
                            self.pool_dec(ch, cid);
                        }
                    }
                }
                self.alive[f] = false;
                continue;
            }
            let corner = match self.ids[f].iter().position(|&v| v == src) {
                Some(c) => c,
                None => return None,
            };
            let mask = self.sub_mask[self.sub[f] as usize];
            for c in 0..2usize {
                if mask >> (3 + c) & 1 == 0 {
                    continue;
                }
                let gradient = match self.uv_gradient(f, c) {
                    Some(g) => g,
                    None => return None,
                };
                let k = self.uvk[c];
                let base = (f * 3 + corner) * k;
                for m in 0..k {
                    let value = self.uv[c][base + m];
                    let new_value = value + dot3(gradient[m], delta);
                    let change = (value - new_value).abs();
                    if change > self.max_uv {
                        self.max_uv = change;
                    }
                    self.uv[c][base + m] = new_value;
                }
                let key = self.uv_key(c, f, corner);
                let new_id = self.intern(3 + c, key);
                let old_id = self.cid[3 + c][f * 3 + corner];
                if new_id != old_id {
                    self.pool_dec(3 + c, old_id);
                    self.pool_inc(3 + c, new_id);
                    self.cid[3 + c][f * 3 + corner] = new_id;
                }
            }
            // position pool: corner now references dst
            let old_pos = self.cid[0][f * 3 + corner];
            let new_pos = self.vertex_pos_id(d);
            if old_pos != new_pos {
                self.pool_dec(0, old_pos);
                self.pool_inc(0, new_pos);
                self.cid[0][f * 3 + corner] = new_pos;
            }
            self.ids[f][corner] = dst;
            let id = self.ids[f];
            match normal_of([
                self.pos[id[0] as usize],
                self.pos[id[1] as usize],
                self.pos[id[2] as usize],
            ]) {
                Some(n) => self.fnormal[f] = n,
                None => return None,
            }
            set_insert(&mut self.incident[d], fid);
        }
        self.incident[s].clear();
        let moved = std::mem::take(&mut self.planes[s]);
        for p in moved {
            if !self.planes[d].iter().any(|q| q == &p) {
                self.planes[d].push(p);
            }
        }
        self.collapses += 1;
        self.since_pack += 1;
        if error > self.max_error {
            self.max_error = error;
        }
        for &v in &touched {
            self.versions[v as usize] += 1;
        }
        Some(touched)
    }

    fn vertex_pos_id(&mut self, v: usize) -> u32 {
        let p = self.pos[v];
        let key = [f32_bits(p[0]), f32_bits(p[1]), f32_bits(p[2]), 0];
        self.intern(0, key)
    }

    fn finish(&mut self) {
        self.out_ids = Vec::with_capacity(self.nf * 3);
        for f in 0..self.nf {
            if self.alive[f] {
                self.out_ids.extend_from_slice(&self.ids[f]);
            } else {
                self.out_ids.extend_from_slice(&[NONE, NONE, NONE]);
            }
        }
        let stride = self.uvk[0] + self.uvk[1];
        self.out_uv = vec![0.0; self.nf * 3 * stride];
        for corner in 0..self.nf * 3 {
            for c in 0..2 {
                let k = self.uvk[c];
                let off = if c == 0 { 0 } else { self.uvk[0] };
                for m in 0..k {
                    self.out_uv[corner * stride + off + m] = self.uv[c][corner * k + m];
                }
            }
        }
        let after = self.comp_count.iter().filter(|&&n| n > 0).count();
        self.out_stats = [
            self.collapses as f64,
            self.max_error,
            self.max_uv,
            self.work_used as f64,
            after as f64,
            if self.limit_hit() { 1.0 } else { 0.0 },
            self.components_before as f64,
            0.0,
        ];
    }

    fn step(&mut self, max_units: u32) -> i32 {
        let mut units = 0u32;
        loop {
            if units >= max_units {
                return 0;
            }
            match self.phase {
                0 => {
                    self.heap.clear();
                    self.cursor = 0;
                    self.phase = 1;
                }
                1 => {
                    if self.cursor < self.nv {
                        let v = self.cursor as u32;
                        self.cursor += 1;
                        self.enqueue(v, LIMITS[self.stage]);
                        units += 1;
                        if self.limit_hit() {
                            self.phase = 2;
                            self.since_pack = 0;
                        }
                    } else {
                        self.phase = 2;
                        self.since_pack = 0;
                    }
                }
                2 => {
                    if self.heap.is_empty() || self.limit_hit() {
                        self.phase = 3;
                        continue;
                    }
                    let Reverse(item) = self.heap.pop().unwrap();
                    units += 1;
                    if self.versions[item.src as usize] != item.vs || self.versions[item.dst as usize] != item.vd {
                        continue;
                    }
                    let limit = LIMITS[self.stage];
                    let error = match self.candidate(item.src, item.dst, limit) {
                        Some(e) => e,
                        None => continue,
                    };
                    let touched = match self.collapse(item.src, item.dst, error) {
                        Some(t) => t,
                        None => return -5,
                    };
                    for v in touched {
                        self.enqueue(v, limit);
                    }
                    if self.since_pack >= 60 {
                        self.since_pack = 0;
                        if self.exact_size() <= self.budget {
                            self.finish();
                            return 1;
                        }
                    }
                }
                _ => {
                    if self.exact_size() <= self.budget || self.limit_hit() {
                        self.finish();
                        return 2;
                    }
                    self.stage += 1;
                    if self.stage >= 4 {
                        self.finish();
                        return 2;
                    }
                    self.phase = 0;
                }
            }
        }
    }
}

// ---------------------------------------------------------------- init

unsafe fn slice<'a, T>(ptr: u32, n: usize) -> &'a [T] {
    std::slice::from_raw_parts(ptr as usize as *const T, n)
}

fn shading_key(vals: &[f64]) -> Option<Key> {
    let mut v = [0.0f64; 4];
    v[..vals.len()].copy_from_slice(vals);
    if vals.len() >= 3 && vals.iter().all(|x| x.is_finite()) {
        let x = py_round(vals[0], 5)?;
        let y = py_round(vals[1], 5)?;
        let z = py_round(vals[2], 5)?;
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

unsafe fn init(header_ptr: u32) -> Result<State, i32> {
    let h: &[u32] = slice(header_ptr, 18);
    if h[0] != 0x4C54_5331 {
        return Err(1);
    }
    let (nv, nf, nsub) = (h[1] as usize, h[2] as usize, h[3] as usize);
    let max_work = if h[4] != 0 { Some(h[5] as u64 | (h[6] as u64) << 32) } else { None };
    let budget = h[7] as u64 | (h[8] as u64) << 32;
    let comps = [h[9] as usize, h[10] as usize, h[11] as usize, h[12] as usize];
    if comps.iter().any(|&c| c > 4) || nsub == 0 || nsub > 4096 {
        return Err(3);
    }
    let pos_raw: &[f64] = slice(h[13], nv * 3);
    let ids_raw: &[u32] = slice(h[14], nf * 3);
    let sub_raw: &[u32] = slice(h[15], nf);
    let mask_raw: &[u32] = slice(h[16], nsub);
    let stride: usize = comps.iter().sum();
    let attr_raw: &[f64] = slice(h[17], nf * 3 * stride);

    let mut pos = Vec::with_capacity(nv);
    for v in 0..nv {
        let p = [pos_raw[v * 3], pos_raw[v * 3 + 1], pos_raw[v * 3 + 2]];
        if !p.iter().all(|x| x.is_finite()) {
            return Err(2);
        }
        pos.push(p);
    }
    let mut ids = Vec::with_capacity(nf);
    for f in 0..nf {
        let id = [ids_raw[f * 3], ids_raw[f * 3 + 1], ids_raw[f * 3 + 2]];
        if id.iter().any(|&v| v as usize >= nv) || sub_raw[f] as usize >= nsub {
            return Err(1);
        }
        ids.push(id);
    }
    let sub_mask: Vec<u32> = mask_raw.to_vec();
    let offs = [0, comps[0], comps[0] + comps[1], comps[0] + comps[1] + comps[2]];
    let uvk = [comps[2], comps[3]];
    let mut uv = [vec![0.0f64; nf * 3 * uvk[0]], vec![0.0f64; nf * 3 * uvk[1]]];
    for f in 0..nf {
        let mask = sub_mask[sub_raw[f] as usize];
        for j in 0..3 {
            for c in 0..2 {
                if mask >> (3 + c) & 1 == 1 {
                    let k = uvk[c];
                    for m in 0..k {
                        let value = attr_raw[(f * 3 + j) * stride + offs[2 + c] + m];
                        if !value.is_finite() {
                            return Err(2);
                        }
                        uv[c][(f * 3 + j) * k + m] = value;
                    }
                }
            }
        }
    }

    let mut st = State {
        nv,
        nf,
        nsub,
        pos,
        ids,
        sub: sub_raw.to_vec(),
        alive: vec![true; nf],
        fnormal: Vec::with_capacity(nf),
        comps,
        uvk,
        uv,
        sub_mask,
        incident: vec![Vec::new(); nv],
        planes: vec![Vec::new(); nv],
        comp_id: vec![0; nf],
        comp_count: Vec::new(),
        components_before: 0,
        versions: vec![0; nv],
        heap: BinaryHeap::new(),
        work_used: 0,
        max_work,
        budget,
        collapses: 0,
        since_pack: 0,
        max_error: 0.0,
        max_uv: 0.0,
        stage: 0,
        phase: 0,
        cursor: 0,
        interner: Default::default(),
        refcnt: Default::default(),
        distinct: [0; 5],
        cid: [vec![NONE; nf * 3], vec![NONE; nf * 3], vec![NONE; nf * 3], vec![NONE; nf * 3], vec![NONE; nf * 3]],
        entry_bytes: [12, comps[0] as u64 * 4, comps[1] as u64 * 4, comps[2] as u64 * 4, comps[3] as u64 * 4],
        rank: Default::default(),
        streams: vec![Vec::new(); nsub * 5],
        out_ids: Vec::new(),
        out_uv: Vec::new(),
        out_stats: [0.0; 8],
    };

    // face normals, incidence, planes
    for f in 0..nf {
        let id = st.ids[f];
        let p = [st.pos[id[0] as usize], st.pos[id[1] as usize], st.pos[id[2] as usize]];
        let normal = normal_of(p).ok_or(1)?;
        st.fnormal.push(normal);
        let distance = dot3(normal, p[0]);
        let plane = [
            zero_norm(py_round(normal[0], 10).ok_or(3)?),
            zero_norm(py_round(normal[1], 10).ok_or(3)?),
            zero_norm(py_round(normal[2], 10).ok_or(3)?),
            zero_norm(py_round(distance, 10).ok_or(3)?),
        ];
        for &v in &id {
            set_insert(&mut st.incident[v as usize], f as u32);
            if !st.planes[v as usize].iter().any(|q| q == &plane) {
                st.planes[v as usize].push(plane);
            }
        }
    }

    // components: faces sharing a vertex and a sub mesh
    let mut parent: Vec<u32> = (0..nf as u32).collect();
    fn find(parent: &mut Vec<u32>, mut i: u32) -> u32 {
        while parent[i as usize] != i {
            parent[i as usize] = parent[parent[i as usize] as usize];
            i = parent[i as usize];
        }
        i
    }
    for v in 0..nv {
        let mut first: HashMap<u32, u32> = HashMap::new();
        for &f in &st.incident[v] {
            let sb = st.sub[f as usize];
            match first.get(&sb) {
                Some(&g) => {
                    let (a, b) = (find(&mut parent, f), find(&mut parent, g));
                    if a != b {
                        parent[a as usize] = b;
                    }
                }
                None => {
                    first.insert(sb, f);
                }
            }
        }
    }
    let mut label: HashMap<u32, u32> = HashMap::new();
    for f in 0..nf {
        let r = find(&mut parent, f as u32);
        let next = label.len() as u32;
        let l = *label.entry(r).or_insert(next);
        st.comp_id[f] = l;
    }
    st.comp_count = vec![0; label.len()];
    for f in 0..nf {
        st.comp_count[st.comp_id[f] as usize] += 1;
    }
    st.components_before = label.len();

    // pool references
    let mut vpid = Vec::with_capacity(nv);
    for v in 0..nv {
        vpid.push(st.vertex_pos_id(v));
    }
    for f in 0..nf {
        let mask = st.sub_mask[st.sub[f] as usize];
        for j in 0..3 {
            let cid0 = vpid[st.ids[f][j] as usize];
            st.cid[0][f * 3 + j] = cid0;
            st.pool_inc(0, cid0);
            for ch in 1..5usize {
                if mask >> ch & 1 == 0 {
                    continue;
                }
                let key = if ch <= 2 {
                    let k = comps[ch - 1];
                    let at = (f * 3 + j) * stride + offs[ch - 1];
                    shading_key(&attr_raw[at..at + k]).ok_or(3)?
                } else {
                    st.uv_key(ch - 3, f, j)
                };
                let id = st.intern(ch, key);
                st.cid[ch][f * 3 + j] = id;
                st.pool_inc(ch, id);
            }
        }
    }
    Ok(st)
}

// ---------------------------------------------------------------- exports

#[no_mangle]
pub extern "C" fn sl_alloc(size: u32) -> u32 {
    let n = ((size as usize) + 7) / 8;
    let v: Vec<u64> = vec![0u64; n.max(1)];
    let p = v.as_ptr() as usize as u32;
    std::mem::forget(v);
    p
}

#[no_mangle]
pub extern "C" fn sl_init(header_ptr: u32) -> i32 {
    unsafe {
        match init(header_ptr) {
            Ok(state) => {
                STATE = Box::into_raw(Box::new(state));
                0
            }
            Err(code) => code,
        }
    }
}

#[no_mangle]
pub extern "C" fn sl_step(max_units: u32) -> i32 {
    unsafe {
        if STATE.is_null() {
            return -1;
        }
        (*STATE).step(max_units.max(1))
    }
}

#[no_mangle]
pub extern "C" fn sl_out_faces() -> u32 {
    unsafe { (*STATE).out_ids.as_ptr() as usize as u32 }
}

#[no_mangle]
pub extern "C" fn sl_out_uv() -> u32 {
    unsafe { (*STATE).out_uv.as_ptr() as usize as u32 }
}

#[no_mangle]
pub extern "C" fn sl_out_stats() -> u32 {
    unsafe { (*STATE).out_stats.as_ptr() as usize as u32 }
}

/// Test hook: CPython-exact `round(x, nd)`; NaN when unsupported.
#[no_mangle]
pub extern "C" fn sl_dbg_round(x: f64, nd: u32) -> f64 {
    py_round(x, nd).unwrap_or(f64::NAN)
}
