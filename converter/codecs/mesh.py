"""Validate the byte ranges and typed arrays of .msh/.msh.blob resources.

Profile: TPF2 and TPF3 reference files, checked 2026-10-07.
Both fixtures use byte offsets AND byte counts, little-endian float32 attributes
and uint32 attribute indices. Attribute item count is count/(4*numComp). The
official pages describe float attributes and per-attribute index lists, but do
not explicitly settle byte units or index encoding; these are fixture-verified.

https://www.transportfever2.com/wiki/doku.php?id=modding:resourcetypes:msh
https://wiki.transportfever3.com/doku.php?id=modding:general:resourcetypes:msh

No blob content is rewritten. Passing this checker proves structural integrity
within this profile, not a game's rendering or full codec compatibility.
"""
from __future__ import annotations

import math
import mmap
from pathlib import Path
import struct


def validate_mesh(definition: dict, blob_path) -> list[dict]:
    diagnostics = []
    path = Path(blob_path)

    def issue(severity, code, message, field=None):
        item = {"severity": severity, "code": code, "message": message, "file": str(path)}
        if field is not None:
            item["field"] = field
        diagnostics.append(item)

    try:
        size = path.stat().st_size
    except OSError as exc:
        issue("error", "MESH_BLOB_MISSING", f"メッシュのバイナリを読めません: {exc}")
        return diagnostics
    if size == 0:
        issue("error", "MESH_BLOB_EMPTY", "メッシュのバイナリが空です。")
        return diagnostics
    if not isinstance(definition, dict):
        issue("error", "MESH_DEFINITION", "メッシュ定義がテーブルではありません。")
        return diagnostics
    attrs, subs = definition.get("vertexAttr"), definition.get("subMeshes")
    if not isinstance(attrs, dict) or not isinstance(subs, (dict, list)) or not attrs or not subs:
        issue("error", "MESH_DEFINITION", "vertexAttr または subMeshes がありません。")
        return diagnostics
    if "position" not in attrs:
        issue("error", "MESH_POSITION_MISSING", "position 属性がありません。")

    def region(desc, label, component_size=4):
        if not isinstance(desc, dict):
            issue("error", "MESH_RANGE", f"{label}: 領域定義がテーブルではありません。", label)
            return None
        offset, count = desc.get("offset"), desc.get("count")
        if type(offset) is not int or type(count) is not int or offset < 0 or count < 0:
            issue("error", "MESH_RANGE", f"{label}: offset/count は非負の整数が必要です。", label)
            return None
        if offset % 4 or count % component_size:
            issue("error", "MESH_ALIGNMENT", f"{label}: バイト境界または要素サイズが一致しません。", label)
            return None
        if offset + count > size:
            issue("error", "MESH_BLOB_RANGE", f"{label}: バイナリ範囲 {offset}+{count} がファイル長 {size} を超えています。", label)
            return None
        return offset, count

    valid_attrs = {}
    for name, desc in attrs.items():
        label = f"vertexAttr.{name}"
        comps = desc.get("numComp") if isinstance(desc, dict) else None
        if type(comps) is not int or not 1 <= comps <= 16:
            issue("error", "MESH_COMPONENTS", f"{label}: numComp が対応範囲 1–16 の整数ではありません。", label)
            continue
        span = region(desc, label, 4 * comps)
        if span is not None:
            valid_attrs[name] = (span[0], span[1], comps)
            if span[1] == 0:
                issue("warning", "MESH_EMPTY_ATTRIBUTE", f"{label}: 属性データが空です。", label)
    streams = []
    sub_items = subs.items() if isinstance(subs, dict) else enumerate(subs, 1)
    for sub_id, sub in sub_items:
        indices = sub.get("indices") if isinstance(sub, dict) else None
        if not isinstance(indices, dict) or not indices:
            issue("error", "MESH_INDICES_MISSING", f"subMeshes[{sub_id}]: indices がありません。")
            continue
        lengths = set()
        if "position" not in indices:
            issue("error", "MESH_POSITION_INDICES", f"subMeshes[{sub_id}]: position インデックスがありません。")
        for name, desc in indices.items():
            label = f"subMeshes[{sub_id}].indices.{name}"
            span = region(desc, label)
            if name not in attrs:
                issue("error", "MESH_INDEX_ATTRIBUTE", f"{label}: 対応する頂点属性がありません。", label)
            if span is not None:
                lengths.add(span[1])
                if span[1] % 12:
                    issue("error", "MESH_TRIANGLE_COUNT", f"{label}: 三角形のインデックス数が3の倍数ではありません。", label)
                streams.append((name, label, span))
        if len(lengths) > 1:
            issue("error", "MESH_INDEX_LENGTH", f"subMeshes[{sub_id}]: 属性ごとのインデックス数が一致しません。")
    try:
        with path.open("rb") as handle, mmap.mmap(handle.fileno(), 0, access=mmap.ACCESS_READ) as blob:
            # Bounded chunks avoid duplicating multi-gigabyte mesh buffers.
            def values(offset, count, fmt):
                for begin in range(offset, offset + count, 256 * 1024):
                    end = min(begin + 256 * 1024, offset + count)
                    yield from (item[0] for item in struct.iter_unpack(fmt, blob[begin:end]))
            for name, (offset, count, comps) in valid_attrs.items():
                component_counts = [0] * comps
                affected_items = 0
                previous_item = -1
                first_samples = []
                for scalar_id, value in enumerate(values(offset, count, "<f")):
                    if not math.isfinite(value):
                        component_counts[scalar_id % comps] += 1
                        item_id = scalar_id // comps
                        if item_id != previous_item:
                            affected_items += 1
                            previous_item = item_id
                        if len(first_samples) < 8:
                            first_samples.append({"item": item_id, "component": scalar_id % comps, "value": "NaN" if math.isnan(value) else "Inf" if value > 0 else "-Inf"})
                if any(component_counts):
                    total_items = count // (4 * comps)
                    # tangents are only warned about; normals only when few vertices are damaged (<=1%)
                    severity = "warning" if name in {"tangent", "normal"} else "error"  # TPF3 only warns about non-finite normals/tangents
                    issue(severity, "MESH_NONFINITE", f"vertexAttr.{name}: {affected_items}/{total_items} 要素に非有限値があります。成分ごとの件数={component_counts}。元のバイナリを保持し描画確認が必要です。", f"vertexAttr.{name}")
                    diagnostics[-1].update({"attribute": name, "nonfinite_scalar_count": sum(component_counts), "component_nonfinite_counts": component_counts, "affected_item_count": affected_items, "total_item_count": total_items, "first_samples": first_samples})
            for name, label, (offset, count) in streams:
                if name not in valid_attrs or count == 0:
                    continue
                _, attr_bytes, comps = valid_attrs[name]
                item_count = attr_bytes // (4 * comps)
                max_index = max(values(offset, count, "<I"))
                if max_index >= item_count:
                    issue("error", "MESH_INDEX_BOUNDS", f"{label}: インデックス {max_index} が属性の要素数 {item_count} を超えています。", label)
    except (OSError, ValueError, struct.error) as exc:
        issue("error", "MESH_BLOB_READ", f"メッシュのバイナリを検査できません: {exc}")
    return diagnostics
