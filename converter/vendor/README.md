# meshoptimizer v1.2

meshoptimizer_v1_2.wasm は公式v1.2の js/meshopt_simplifier.js に内蔵された
WASMを、そのJSのunpackアルゴリズムで展開したものです。

Source: https://github.com/zeux/meshoptimizer/tree/v1.2
License: MIT (同梱 LICENSES/meshoptimizer-MIT.txt)
SHA256: 0c1c462dc0bcd02bfea0e45cf8f76d56875c584ed4dcc8e5296a44972400f7cd

実行時にハッシュを検査し、ホストのファイル・ネットワーク等のimportを
許可しません。wasmtime 49.0.0により実行します。
