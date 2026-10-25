# TPF2 → TPF3 車両MOD変換ツール

Transport Fever 2（TPF2）の鉄道車両MODを、Transport Fever 3（TPF3）で使える形に変換するWindows用ツールです。MODのフォルダまたはZIPを渡すと、TPF3用のパッケージと日本語の変換レポートを出力します。元のMODは変更しません。

非公式のツールです。Urban Gamesとは関係ありません。ゲーム本体、第三者のMOD、ゲームの音声・モデルなどの素材は含みません。

## 想定する使い方と免責

- 主な想定は、**MODの作者が、自分で作ったTPF2のMODをTPF3向けに変換する**使い方です。
- 他の人が作ったMODを変換することは、本ツールが推奨するものではありません。行う場合は**自己責任**で、そのMODの作者が定めた利用条件に従ってください。変換後の出力には、元のMODの素材（形式を変換したもの）が含まれます。個人利用にとどめ、再配布する場合は元の作者の許可を得てください。
- 自分のMODか他人のMODかを問わず、本ツールの利用や、変換結果の利用・配布について、本ツールの作者は責任を負いません。本ソフトウェアは現状のまま、無保証で提供します。

## 状況

- 変換後の車両がTPF3の車庫に表示され、購入できることは、実機で確認しています。
- カント、ドア、台車、サウンド、前後灯など、車種ごとの細かな動作の確認は一部の車両にとどまります。変換後は、モデルエディタとゲームで確認してください。
- 対応できない機能は黙って捨てずに、レポートに「要手動対応」として記録し、部分的な出力は完成品と分けて置きます。「静的検証に合格」は「ゲーム内で確認済み」ではありません。

## 使い方（ビルド済みの配布版）

1. [Releases](../../releases)から`TPF3VehicleConverter-Windows-x64.zip`をダウンロードして、すべて展開します（`_internal`フォルダも必要です）。
2. `TPF3VehicleConverter.exe`を起動します。Pythonは不要です。
3. 出力先を指定し、MODのフォルダまたはZIPを画面へドラッグ＆ドロップします。
4. 「結果フォルダを開く」「レポートを開く」で結果を確認します。

標準素材を参照するMODは、変換前に「TPF2本体」でインストール先（またはres）を指定してください。足りない参照資産だけを読み取って出力に同封します（本体は書き換えません）。

出力は実行ごとの`run_...`フォルダに作られます。`reports/conversion_report.md`に変換結果、警告、手動対応事項、ゲーム内で確認する項目が入ります。`package`が変換結果です。成功したMODは`mods_ready\<ID>_<車両名>\`にも並び、そのままゲームの`mods`フォルダへコピーできます。要手動対応のものは`mods_ready`に入れません。

## 結果の読み方

| 結果 | 内容 |
| --- | --- |
| 出力生成・静的検証合格 | パッケージ構造と対応済み設定の検査に合格。ゲーム内確認が必要 |
| 出力生成・要確認 | 警告をレポートで確認し、モデルエディタ／ゲームで検証 |
| 要手動対応 | 必須処理に未対応・不明な点がある。部分出力と修正事項を確認 |
| 失敗／入力エラー | 破損、入力欠落、処理異常。詳細欄を確認 |

## コマンドライン

同梱の`TPF3VehicleConverterCLI.exe`はコンソール版で、GUI版と同じエンジンを使います。

```powershell
.\TPF3VehicleConverterCLI.exe analyze 'D:\MOD\車両_1' --json 'D:\結果\analysis.json'
.\TPF3VehicleConverterCLI.exe plan 'D:\MOD\車両_1'
.\TPF3VehicleConverterCLI.exe convert 'D:\MOD\車両_1.zip' --output 'D:\結果'
.\TPF3VehicleConverterCLI.exe convert 'D:\MOD\車両_1.zip' --output 'D:\結果' --game-root 'D:\SteamLibrary\steamapps\common\Transport Fever 2'
.\TPF3VehicleConverterCLI.exe batch 'D:\MOD' --output 'D:\結果' --mod-workers 4
.\TPF3VehicleConverterCLI.exe validate 'D:\結果\run_...\package\変換MOD'
```

- `batch`は、フォルダ直下のMODをまとめて変換します。車両でないMODは理由つきで対象外にします。`--only フォルダ名,…`で絞り込めます。`--mod-workers`で複数のMODを同時に変換できます。
- `--texture-workers`で画像変換の並列数を変えられます（既定8、最大64）。プロセス数は両方の積になります。
- `--singles hide|group|show`で、編成専用の部品の扱いを選びます（既定は`hide`）。
- 終了コード: `0`＝処理終了（警告はレポートで確認）、`1`＝失敗、`2`＝要手動対応・部分出力・取消し、`130`＝Ctrl+Cによる中断。

## ソースから実行・ビルド

Python 3.12以上（Tkinter付き）が必要です。

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-build.txt
.\.venv\Scripts\python.exe -m converter gui
.\.venv\Scripts\python.exe -m converter convert 'D:\MOD\車両_1' --output 'D:\結果'
.\.venv\Scripts\python.exe -m unittest discover -s tests
```

配布用のEXEとZIPは次で作ります（`release\`に出力）。

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\build_portable.ps1
```

EXEは未署名です。`converter/vendor/`のWASM（meshoptimizer、自作のstrict_lod）はビルド済みで、Rustは再ビルド時だけ必要です（`tools/strict_lod/README.md`）。

## ライセンス

本ツールのコードはMITライセンスです（`LICENSE`）。

## 第三者のソフトウェア

meshoptimizer（MIT）、Pillow、wasmtime、lupa、tkinterdnd2、PyInstaller、Python/Tcl/Tkを使っています。配布ZIPの`licenses`フォルダと、`LICENSES`フォルダに各ライセンスを収めています。
