# strict_lod (Rust -> WASM)

Native helpers for the LOD generator. Output of both parts is byte-identical to the pure-Python code they mirror
(`lod.simplify_mesh`, and the `lod._pack` size check inside `meshopt._simplify_meshopt_reference`).
The built module is committed as `converter/vendor/strict_lod.wasm` (no imports, SHA-256 pinned in
`converter/transforms/strict_lod.py`). Users never need Rust; only rebuilds do.

Rebuild (needs Rust, `rustup target add wasm32-unknown-unknown`):

    cd tools/strict_lod
    cargo build --release --target wasm32-unknown-unknown
    copy target\wasm32-unknown-unknown\release\strict_lod.wasm ..\..\converter\vendor\strict_lod.wasm

then update `STRICT_LOD_WASM_SHA256` in `converter/transforms/strict_lod.py` and run
`python -m unittest discover -s tests -p "test_strict_lod.py"` and `-p "test_meshopt_fast.py"`.
ABI: INTERFACE.md. Disable the native meshopt trial evaluator with env `TPF3_NO_NATIVE_PACK=1`.
