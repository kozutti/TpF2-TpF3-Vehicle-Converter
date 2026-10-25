from __future__ import annotations
import json
from pathlib import Path
from datetime import datetime, timezone

def json_safe(value):
    if isinstance(value, dict):
        return {str(k): json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(v) for v in value]
    if isinstance(value, Path):
        return str(value)
    return value

def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(json_safe(value), ensure_ascii=False, indent=2), encoding='utf-8')

def write_reports(run_dir, result, analysis, plan, manifest):
    reports = Path(run_dir) / 'reports'
    reports.mkdir(parents=True, exist_ok=True)
    write_json(reports / 'analysis.json', analysis)
    write_json(reports / 'conversion_plan.json', plan)
    write_json(reports / 'manifest.json', manifest)
    write_json(reports / 'result.json', result)
    status = {'partial': '要手動対応', 'success': '出力生成・静的検証合格（ゲーム内未確認）',
              'cancelled': '取消し', 'failed': '失敗'}.get(result['status'], result['status'])
    lines = ['# TPF2 → TPF3 変換レポート', '', f'結果: **{status}**',
             f'入力: `{analysis.get("source", "")}`', f'車両: {analysis.get("vehicle_count", 0)}件',
             f'出力: `{result.get("package_dir", "")}`', '',
             '元MODは変更していません。ゲーム・モデルエディタでの動作確認は未実施です。', '',
             '## 診断', '', '|重要度|コード|ファイル|内容|', '|---|---|---|---|']
    for entry in result['diagnostics']:
        values = [entry.get('severity', ''), entry.get('code', ''), entry.get('file', ''), entry.get('message', '')]
        lines.append('|' + '|'.join(str(v).replace('|', '\\|').replace('\n', '<br>') for v in values) + '|')
    lines += ['', '## ゲーム内確認', '', '- [ ] MOD認識・車両購入・年代・編成の前後方向',
              '- [ ] 車体・車輪・台車・すべてのLOD・昼夜の材質',
              '- [ ] 左右ホームでドア開閉、閉じ切ってから出発',
              '- [ ] カント付き曲線で車体・台車・乗客が同じ傾斜を追従',
              '- [ ] 加減速・サウンド・連結部品・乗降・保存と再読込', '',
              '未対応の元定義と値はanalysis.jsonに保持しています。partialはそのままゲームへ入れず、診断項目を修正してください。']
    (reports / 'conversion_report.md').write_text('\n'.join(lines), encoding='utf-8')
    with (reports / 'conversion_log.jsonl').open('w', encoding='utf-8') as stream:
        for entry in result['diagnostics']:
            stream.write(json.dumps({'run_id': result['run_id'], 'time': datetime.now(timezone.utc).isoformat(),
                                     'stage': 'validation', **entry}, ensure_ascii=False) + '\n')
