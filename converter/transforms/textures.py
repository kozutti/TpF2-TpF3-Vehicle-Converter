"""DDS format conversion for TPF3 texture slots, without an external process.

BC3 opaque colour blocks can be reused as BC1, because their RGB encoding is
the same. Normal BC1 is decoded as conventional RGB tangent-space XYZ and
encoded as two BC4 channels (BC5). No green-channel inversion is performed.
Every supplied mip is converted; a BC1/BC2/BC3/BC5 file that has a single level gets the rest of the chain (TPF3 warns
about a compressed texture without mipmaps). Neither DDS arrays nor packed DXT5nm normals are guessed. Invalid RGB normals may use explicitly declared RG as a fallback.
Source files are never modified.
"""
from __future__ import annotations

import hashlib
import math
from pathlib import Path
import struct


class DDSConversionError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


_FOURCC_FORMATS = {b"DXT1": "BC1", b"DXT3": "BC2", b"DXT5": "BC3", b"ATI2": "BC5", b"BC5U": "BC5"}
_DXGI_FORMATS = {71: "BC1", 72: "BC1", 74: "BC2", 75: "BC2", 77: "BC3", 78: "BC3", 83: "BC5", 98: "BC7", 99: "BC7"}
_BLOCK_SIZE = {"BC1": 8, "BC2": 16, "BC3": 16, "BC5": 16, "BC7": 16}
_TARGET_ROLES = {
    "opaque_albedo": "BC1", "albedo": "BC1", "map_albedo": "BC1",
    "metal_gloss_ao": "BC1", "map_metal_gloss_ao": "BC1",
    "normal": "BC5", "map_normal": "BC5",
    "transparent_albedo": "BC3",
}


def _diagnostic(code, message, path, severity="error"):
    return {"severity": severity, "code": code, "message": message, "file": str(path)}


_RAW_DXGI = {28: "rgba", 29: "rgba", 87: "bgra", 91: "bgra", 88: "bgrx"}
_SRGB_DXGI = {72, 75, 78, 29, 91}


def _raw_layout(data: bytes, dxgi):
    """Byte index of each 8-bit channel of an uncompressed DDS, or None if it is not a plain 8-bit layout."""
    if dxgi is not None:
        order = _RAW_DXGI.get(dxgi)
        if not order:
            return None
        return {"bpp": 4, "r": order.index("r"), "g": order.index("g"), "b": order.index("b"),
                "a": order.index("a") if "a" in order else None}
    flags = struct.unpack_from("<I", data, 80)[0]
    bit_count = struct.unpack_from("<I", data, 88)[0]
    red, green, blue, alpha = struct.unpack_from("<4I", data, 92)
    if not flags & 0x40 or bit_count not in (24, 32):
        return None
    positions = {}
    for name, mask in (("r", red), ("g", green), ("b", blue), ("a", alpha if flags & 1 else 0)):
        if not mask:
            positions[name] = None
            continue
        shift = (mask & -mask).bit_length() - 1
        if mask != 0xFF << shift or shift % 8:
            return None
        positions[name] = shift // 8
    if None in (positions["r"], positions["g"], positions["b"]):
        return None
    used = [value for value in positions.values() if value is not None]
    if len(set(used)) != len(used) or max(used) >= bit_count // 8:
        return None
    return {"bpp": bit_count // 8, **positions}


def _parse_dds(data: bytes, allow_raw: bool = False) -> dict:
    if len(data) < 128 or data[:4] != b"DDS " or struct.unpack_from("<I", data, 4)[0] != 124:
        raise DDSConversionError("DDS_HEADER_INVALID", "DDSヘッダーが破損しています。")
    if struct.unpack_from("<I", data, 76)[0] != 32:
        raise DDSConversionError("DDS_HEADER_INVALID", "DDSピクセル形式のサイズが不正です。")
    height, width, _, depth, mip_count = struct.unpack_from("<5I", data, 12)
    mip_count = max(1, mip_count)
    caps2 = struct.unpack_from("<I", data, 112)[0]
    if caps2 & (0xFE00 | 0x200000) or depth > 1:
        raise DDSConversionError("DDS_TEXTURE_DIMENSION_UNSUPPORTED", "キューブ／3D DDSは自動変換できません。")
    if not width or not height or width > 32768 or height > 32768 or width * height > 268435456:
        raise DDSConversionError("DDS_DIMENSION_INVALID", "DDSの画像サイズが不正または上限を超えています。")
    if mip_count > max(width, height).bit_length():
        raise DDSConversionError("DDS_MIP_COUNT_INVALID", "DDSのmipmap数が画像サイズに適合しません。")
    fourcc = data[84:88]
    dxgi = None
    offset = 128
    if fourcc == b"DX10":
        if len(data) < 148:
            raise DDSConversionError("DDS_HEADER_INVALID", "DDS DX10拡張ヘッダーが欠落しています。")
        dxgi, dimension, misc, array_size, misc2 = struct.unpack_from("<5I", data, 128)
        if dimension != 3 or array_size != 1 or misc & 4:
            raise DDSConversionError("DDS_TEXTURE_DIMENSION_UNSUPPORTED", "DDS配列／キューブ／3Dテクスチャは自動変換できません。")
        format_name = _DXGI_FORMATS.get(dxgi)
        offset = 148
    else:
        format_name = _FOURCC_FORMATS.get(fourcc)
    raw = None
    if format_name and fourcc != b"DX10" and not struct.unpack_from("<I", data, 80)[0] & 4:
        format_name = None
    if not format_name and allow_raw:
        raw = _raw_layout(data, dxgi)
        if raw:
            format_name = "RAW"
    if not format_name:
        raise DDSConversionError("DDS_FORMAT_UNSUPPORTED", f"DDS形式 {fourcc!r} / DXGI {dxgi} は未対応です。")
    mips = []
    mip_width, mip_height = width, height
    for level in range(mip_count):
        blocks_x, blocks_y = (mip_width + 3) // 4, (mip_height + 3) // 4
        size = mip_width * mip_height * raw["bpp"] if raw else blocks_x * blocks_y * _BLOCK_SIZE[format_name]
        if offset + size > len(data):
            raise DDSConversionError("DDS_PAYLOAD_TRUNCATED", f"DDSのmipmap {level} データが不足しています。")
        mips.append({"level": level, "width": mip_width, "height": mip_height,
                     "offset": offset, "size": size, "blocks_x": blocks_x, "blocks_y": blocks_y})
        offset += size
        mip_width, mip_height = max(1, mip_width // 2), max(1, mip_height // 2)
    if offset != len(data):
        raise DDSConversionError("DDS_TRAILING_DATA", "DDSに不明な末尾データがあるため自動変換を停止しました。")
    return {"format": format_name, "width": width, "height": height,
            "mip_count": mip_count, "dxgi_format": dxgi,
            "fourcc": fourcc.decode("ascii", errors="replace"), "raw": raw,
            "header_size": 148 if dxgi is not None else 128, "mips": mips}


def inspect_dds(path: Path, allow_raw: bool = False) -> dict:
    """Inspect exact DDS mip boundaries; raises DDSConversionError on unknown data.

    allow_raw also accepts uncompressed 8-bit RGB/RGBA input (reported as format "RAW"); converted output never is.
    """
    return _parse_dds(Path(path).read_bytes(), allow_raw=allow_raw)


def _rgb565(value: int):
    r, g, b = (value >> 11) & 31, (value >> 5) & 63, value & 31
    return ((r << 3) | (r >> 2), (g << 2) | (g >> 4), (b << 3) | (b >> 2))


def _decode_bc1(block: bytes, force_four_colors=False):
    c0, c1, indices = struct.unpack("<HHI", block)
    first, second = _rgb565(c0), _rgb565(c1)
    palette = [(*first, 255), (*second, 255)]
    if c0 > c1 or force_four_colors:
        palette.extend([tuple((2 * a + b) // 3 for a, b in zip(first, second)) + (255,),
                        tuple((a + 2 * b) // 3 for a, b in zip(first, second)) + (255,)])
    else:
        palette.extend([tuple((a + b) // 2 for a, b in zip(first, second)) + (255,), (0, 0, 0, 0)])
    return [palette[(indices >> (2 * i)) & 3] for i in range(16)]


def _bc4_palette(first: int, second: int):
    if first > second:
        return [first, second] + [((7 - i) * first + i * second) // 7 for i in range(1, 7)]
    return [first, second] + [((5 - i) * first + i * second) // 5 for i in range(1, 5)] + [0, 255]


def _decode_bc4(block: bytes):
    palette = _bc4_palette(block[0], block[1])
    indices = int.from_bytes(block[2:8], "little")
    return [palette[(indices >> (3 * index)) & 7] for index in range(16)]


def _encode_bc4(values):
    first, second = max(values), min(values)
    palette = _bc4_palette(first, second)
    indices = 0
    for index, value in enumerate(values):
        choice = min(range(8), key=lambda position: abs(palette[position] - value))
        indices |= choice << (index * 3)
    return bytes((first, second)) + indices.to_bytes(6, "little")


def _bc3_colour_to_bc1(block: bytes):
    # BC3 always uses the four-colour palette. BC1 needs c0 > c1 to do so.
    c0, c1, indices = struct.unpack("<HHI", block[8:16])
    if c0 > c1:
        return block[8:16]
    if c0 < c1:
        # Exchange endpoints and palette positions 0↔1, 2↔3 exactly.
        return struct.pack("<HHI", c1, c0, indices ^ 0x55555555)
    # All four RGB values are identical. Keep that exact RGB565 colour as
    # one endpoint and point every texel to it; the other endpoint is unused.
    if c0:
        return struct.pack("<HHI", c0, c0 - 1, 0)
    return struct.pack("<HHI", 1, 0, 0x55555555)


def _normal_bc1_to_bc5(block: bytes, valid_width: int, valid_height: int):
    rgb = _decode_bc1(block)
    x_values, y_values = [], []
    z_clamped = False
    for position in range(16):
        x, y = position % 4, position // 4
        # Edge replication excludes padding bytes from normal-layout checks.
        x, y = min(x, valid_width - 1), min(y, valid_height - 1)
        red, green, blue, alpha = rgb[y * 4 + x]
        if alpha != 255:
            raise DDSConversionError("NORMAL_LAYOUT_UNKNOWN", "normalのBC1透明色をXYZ法線として解釈できません。")
        nx, ny, nz = red / 127.5 - 1, green / 127.5 - 1, blue / 127.5 - 1
        # BC1's blue endpoint has only five bits. A tangent normal at grazing
        # angle can land slightly below zero after quantization. Permit one
        # RGB565 blue quantization interval and report that correction.
        if nz < -(8 / 127.5):
            raise DDSConversionError("NORMAL_LAYOUT_UNKNOWN", "normalに負のZがあり、通常RGB法線か不明です。DXT5nm等は手動対応が必要です。")
        if nz < 0:
            nz = 0
            z_clamped = True
        length = math.sqrt(nx * nx + ny * ny + nz * nz)
        if length < 0.05:
            raise DDSConversionError("NORMAL_LAYOUT_UNKNOWN", "normalに法線ベクトルとして不正な値があります。")
        nx, ny = nx / length, ny / length
        x_values.append(max(0, min(255, round((nx + 1) * 127.5))))
        y_values.append(max(0, min(255, round((ny + 1) * 127.5))))
    return _encode_bc4(x_values) + _encode_bc4(y_values), z_clamped


def _declared_rg_normal(block: bytes, valid_width: int, valid_height: int, four_colors=False):
    """Preserve declared XY channels; the shader reconstructs positive Z."""
    pixels = _decode_bc1(block, four_colors)
    red, green = [], []
    for position in range(16):
        x = min(position % 4, valid_width - 1)
        y = min(position // 4, valid_height - 1)
        r, g, _, alpha = pixels[y * 4 + x]
        if alpha != 255:
            raise DDSConversionError('NORMAL_LAYOUT_UNKNOWN', 'RG法線のBC1透明色は自動変換できません。')
        red.append(r)
        green.append(g)
    return _encode_bc4(red) + _encode_bc4(green)


_BC4_INDEX = (0, 2, 3, 4, 5, 6, 7, 1)


def _encode_bc4_fast(values):
    """BC4 block from 16 values: range endpoints and arithmetic index assignment (no palette search)."""
    high, low = max(values), min(values)
    span = high - low
    if not span:
        return bytes((high, low, 0, 0, 0, 0, 0, 0))
    bits = 0
    shift = 0
    for value in values:
        bits |= _BC4_INDEX[((high - value) * 14 + span) // (2 * span)] << shift
        shift += 3
    return bytes((high, low)) + bits.to_bytes(6, "little")


def _rgb565_of(red, green, blue):
    return ((red >> 3) << 11) | ((green >> 2) << 5) | (blue >> 3)


def _encode_bc1_rgb(pixels):
    """BC1 four-colour block from 16 (r, g, b) tuples: bounding-box endpoints, projected indices."""
    reds = [p[0] for p in pixels]
    greens = [p[1] for p in pixels]
    blues = [p[2] for p in pixels]
    c0 = _rgb565_of(max(reds), max(greens), max(blues))
    c1 = _rgb565_of(min(reds), min(greens), min(blues))
    if c0 == c1:
        if c0:
            return struct.pack("<HHI", c0, c0 - 1, 0)
        return struct.pack("<HHI", 1, 0, 0x55555555)
    if c0 < c1:
        c0, c1 = c1, c0
    (r0, g0, b0), (r1, g1, b1) = _rgb565(c0), _rgb565(c1)
    dr, dg, db = r0 - r1, g0 - g1, b0 - b1
    scale = dr * dr + dg * dg + db * db or 1
    indices = 0
    for position, (red, green, blue) in enumerate(pixels):
        # t runs from 0 (at c1) to 3 (at c0); BC1 codes: 0 = c0, 1 = c1, 2 = 2/3 c0 + 1/3 c1, 3 = 1/3 c0 + 2/3 c1.
        t = ((red - r1) * dr + (green - g1) * dg + (blue - b1) * db) * 3 / scale
        step = 0 if t < 0 else 3 if t > 3 else int(t + 0.5)
        indices |= (1, 3, 2, 0)[step] << (2 * position)
    return struct.pack("<HHI", c0, c1, indices)


def _encode_bc2_alpha(values):
    """BC2 explicit alpha: sixteen 4-bit values, texel 0 in the lowest nibble."""
    bits = 0
    for index, value in enumerate(values):
        bits |= min(15, (value * 15 + 127) // 255) << (4 * index)
    return bits.to_bytes(8, "little")


def _encode_raw_mip(data, mip, raw, target_format, cancel):
    """Encode one uncompressed mip level as BC1 / BC2 / BC3 / BC5 blocks."""
    width, height, bpp = mip["width"], mip["height"], raw["bpp"]
    start, end = mip["offset"], mip["offset"] + mip["size"]
    planes = {name: data[start + raw[name]:end:bpp] for name in ("r", "g", "b")}
    alpha_plane = data[start + raw["a"]:end:bpp] if raw["a"] is not None else None
    out = bytearray()
    for block_y in range(mip["blocks_y"]):
        _check_cancel(cancel)
        rows = [min(block_y * 4 + y, height - 1) * width for y in range(4)]
        for block_x in range(mip["blocks_x"]):
            columns = [min(block_x * 4 + x, width - 1) for x in range(4)]
            positions = [row + column for row in rows for column in columns]
            if target_format == "BC5":
                red, green = planes["r"], planes["g"]
                out += _encode_bc4_fast([red[i] for i in positions]) + _encode_bc4_fast([green[i] for i in positions])
                continue
            if target_format in {"BC2", "BC3"}:
                alpha = alpha_plane if alpha_plane is not None else bytes([255]) * (width * height)
                out += (_encode_bc4_fast if target_format == "BC3" else _encode_bc2_alpha)([alpha[i] for i in positions])
            red, green, blue = planes["r"], planes["g"], planes["b"]
            out += _encode_bc1_rgb([(red[i], green[i], blue[i]) for i in positions])
    return out


def _raw_alpha_used(data, info):
    raw = info["raw"]
    if raw["a"] is None:
        return False
    for mip in info["mips"]:
        plane = data[mip["offset"] + raw["a"]:mip["offset"] + mip["size"]:raw["bpp"]]
        if plane.count(255) != len(plane):
            return True
    return False


def _output_header(data: bytes, info: dict, target_format: str):
    header = bytearray(data[:info["header_size"]])
    flags = struct.unpack_from("<I", header, 8)[0]
    struct.pack_into("<I", header, 8, (flags | 0x80000) & ~8)  # linear-size, not pitch
    first_mip = info["mips"][0]
    struct.pack_into("<I", header, 20, first_mip["blocks_x"] * first_mip["blocks_y"] * _BLOCK_SIZE[target_format])
    struct.pack_into("<I", header, 80, 4)  # DDPF_FOURCC
    # RGB bit count and channel masks are irrelevant for FOURCC formats.
    header[88:108] = b"\0" * 20
    if info["dxgi_format"] is not None:
        srgb = info["dxgi_format"] in _SRGB_DXGI
        dxgi = {"BC1": 72 if srgb else 71, "BC3": 78 if srgb else 77, "BC5": 83}[target_format]
        struct.pack_into("<I", header, 128, dxgi)
        # BC1 opaque slots have no alpha; BC5 alpha is unspecified.
        struct.pack_into("<I", header, 144, 3 if target_format == "BC1" else 0)
    else:
        header[84:88] = {"BC1": b"DXT1", "BC3": b"DXT5", "BC5": b"ATI2"}[target_format]
    return bytes(header)


def _decode_top_mip(data: bytes, info: dict) -> dict:
    """Level 0 of a BC1 / BC3 / BC5 DDS as 8-bit planes (r, g, b, a), one byte per texel."""
    mip = info["mips"][0]
    width, height = mip["width"], mip["height"]
    size = _BLOCK_SIZE[info["format"]]
    planes = {name: bytearray(width * height) for name in "rgba"}
    for block_y in range(mip["blocks_y"]):
        for block_x in range(mip["blocks_x"]):
            offset = mip["offset"] + (block_y * mip["blocks_x"] + block_x) * size
            block = data[offset:offset + size]
            if info["format"] == "BC5":
                values = {"r": _decode_bc4(block[:8]), "g": _decode_bc4(block[8:16])}
                colour = None
            else:
                if info["format"] == "BC2":
                    bits = int.from_bytes(block[:8], "little")
                    values = {"a": [((bits >> (4 * i)) & 15) * 17 for i in range(16)]}
                else:
                    values = {"a": _decode_bc4(block[:8])} if info["format"] == "BC3" else {}
                colour = _decode_bc1(block[-8:], force_four_colors=info["format"] != "BC1")
            for y in range(min(4, height - block_y * 4)):
                for x in range(min(4, width - block_x * 4)):
                    index = (block_y * 4 + y) * width + block_x * 4 + x
                    texel = y * 4 + x
                    if colour is not None:
                        planes["r"][index], planes["g"][index], planes["b"][index] = colour[texel][:3]
                    for name, plane in values.items():
                        planes[name][index] = plane[texel]
            # unused planes: opaque alpha / zero blue
    if info["format"] not in {"BC2", "BC3"}:
        planes["a"] = bytearray([255]) * (width * height)
    return planes


def _complete_mip_chain(output: bytes, target_format: str, cancel=None):
    """Add the missing smaller mip levels to a BC1 / BC2 / BC3 / BC5 DDS (TPF3 does not generate them and warns:
    "does not have any mipmaps ... is sampled with a mipmapping min filter"). Level 0 is kept byte for byte; each added level is
    a 2x2 box average of the one above, encoded in the same format. Returns (data, number of levels added)."""
    if target_format not in {"BC1", "BC2", "BC3", "BC5"}:
        return output, 0
    info = _parse_dds(output)
    full = max(info["width"], info["height"]).bit_length()
    if info["mip_count"] >= full or info["mip_count"] > 1:
        return output, 0  # complete, or the author's own partial chain: leave it
    try:
        from PIL import Image
    except ImportError:
        return output, 0
    planes = _decode_top_mip(output, info)
    width, height = info["width"], info["height"]
    payload = bytearray(output)
    raw = {"bpp": 4, "r": 0, "g": 1, "b": 2, "a": 3}
    added = 0
    while max(width, height) > 1:
        _check_cancel(cancel)
        next_width, next_height = max(1, width // 2), max(1, height // 2)
        planes = {name: bytearray(Image.frombytes("L", (width, height), bytes(plane))
                                  .resize((next_width, next_height), Image.Resampling.BOX).tobytes())
                  for name, plane in planes.items()}
        width, height = next_width, next_height
        buffer = bytearray(width * height * 4)
        for index, name in enumerate("rgba"):
            buffer[index::4] = planes[name]
        mip = {"width": width, "height": height, "offset": 0, "size": len(buffer),
               "blocks_x": (width + 3) // 4, "blocks_y": (height + 3) // 4}
        payload += _encode_raw_mip(buffer, mip, raw, target_format, cancel)
        added += 1
    struct.pack_into("<I", payload, 28, 1 + added)
    struct.pack_into("<I", payload, 8, struct.unpack_from("<I", payload, 8)[0] | 0x20000)   # DDSD_MIPMAPCOUNT
    struct.pack_into("<I", payload, 108, struct.unpack_from("<I", payload, 108)[0] | 0x400008)  # DDSCAPS_MIPMAP | COMPLEX
    return bytes(payload), added


def complete_mips_in_place(path: Path, cancel=None):
    """Complete the mip chain of a DDS inside an output package (never a source file). Returns the number of levels now, or None."""
    path = Path(path)
    data = path.read_bytes()
    try:
        info = _parse_dds(data)
    except DDSConversionError:
        return None
    output, added = _complete_mip_chain(data, info["format"], cancel)
    if not added:
        return None
    path.write_bytes(output)
    return 1 + added


def _cancelled(cancel):
    return cancel is not None and (cancel.is_set() if hasattr(cancel, "is_set") else cancel())


def _check_cancel(cancel):
    if _cancelled(cancel):
        raise InterruptedError("DDS変換を取り消しました。")


def convert_dds(source: Path, target: Path, role: str, cancel=None, normal_layout="rgb") -> dict:
    """Convert a DDS for its material slot; return diagnostics and exact format log.

    Opaque slots ignore alpha; removing non-opaque stored alpha is reported as
    a warning. Transparent albedo keeps BC3. BC3 packed normals are refused.
    On a manual/error result the target is not created. In-place writes fail.
    """
    source, target = Path(source), Path(target)
    result = {"changed": False, "source": str(source), "target": str(target),
              "role": role, "format_before": None, "format_after": None,
              "status": "manual", "diagnostics": []}
    try:
        if source.resolve() == target.resolve():
            raise DDSConversionError("DDS_IN_PLACE_FORBIDDEN", "元DDSには上書きできません。")
        target_format = _TARGET_ROLES.get(role)
        if not target_format:
            raise DDSConversionError("DDS_SLOT_UNKNOWN", f"DDSの用途 {role!r} が不明です。")
        if normal_layout not in {'rgb', 'rgb_with_rg_fallback'}:
            raise DDSConversionError('NORMAL_LAYOUT_UNKNOWN', '法線のチャンネル指定が不正です。')
        _check_cancel(cancel)
        if source.stat().st_size > 512 * 1024**2:
            raise DDSConversionError("DDS_SIZE_LIMIT", "DDSファイルが512 MiB上限を超えています。")
        chunks = []
        with source.open("rb") as handle:
            while chunk := handle.read(8 * 1024**2):
                _check_cancel(cancel)
                chunks.append(chunk)
        data = b"".join(chunks)
        del chunks
        info = _parse_dds(data, allow_raw=True)
        result.update({"format_before": info["format"], "width": info["width"], "height": info["height"],
                       "mip_count": info["mip_count"], "source_sha256": hashlib.sha256(data).hexdigest()})
        if role == "transparent_albedo" and info["format"] in {"BC1", "BC2", "BC3", "BC7"}:
            target_format = info["format"]
        if role in {"normal", "opaque_albedo", "metal_gloss_ao"} and info["format"] == "BC7":
            # No BC7 decoder here: keep the data as it is. TPF3 only recommends BC5 (normal) / BC1 (opaque) and
            # reports a warning, not an error, for other formats.
            target_format = "BC7"
            result["diagnostics"].append(_diagnostic("TEXTURE_BC7_KEPT",
                f"{role} がBC7のため形式を変えずに使います（TPF3の推奨は{'BC5' if role == 'normal' else 'BC1'}です）。", source, "warning"))
        if info["format"] == "RAW":
            if role == "transparent_albedo" and not _raw_alpha_used(data, info):
                target_format = "BC1"
            payload = bytearray()
            for mip in info["mips"]:
                payload += _encode_raw_mip(data, mip, info["raw"], target_format, cancel)
            output = _output_header(data, info, target_format) + payload
            if target_format == "BC1" and role != "transparent_albedo" and _raw_alpha_used(data, info):
                result["diagnostics"].append(_diagnostic("DDS_UNUSED_ALPHA_REMOVED", "opaque/MGA用途で使われないalphaを除去しました。RGBとmipmapは保持しています。", source, "warning"))
            if target_format == "BC5":
                result["normal_layout"] = "RGB_XYZ_to_BC5_XY_no_green_inversion"
            result["diagnostics"].append(_diagnostic("TEXTURE_UNCOMPRESSED_ENCODED",
                f"無圧縮DDSを{target_format}へ圧縮しました（mipmapはすべて保持）。圧縮による軽い劣化があります。", source, "info"))
        elif info["format"] == target_format:
            output = data
        elif target_format == "BC1" and info["format"] in {"BC2", "BC3"}:
            payload = bytearray()
            discarded_alpha = False
            alpha_cache = {}
            for mip in info["mips"]:
                for block_index in range(mip["blocks_x"] * mip["blocks_y"]):
                    if block_index % 128 == 0:
                        _check_cancel(cancel)
                    offset = mip["offset"] + block_index * 16
                    block = data[offset:offset + 16]
                    if not discarded_alpha:
                        bx, by = block_index % mip["blocks_x"], block_index // mip["blocks_x"]
                        valid_w, valid_h = min(4, mip["width"] - bx * 4), min(4, mip["height"] - by * 4)
                        alpha_key = (block[:8], valid_w, valid_h)
                        nonopaque = alpha_cache.get(alpha_key)
                        if nonopaque is None:
                            alphas = (_decode_bc4(block[:8]) if info["format"] == "BC3" else
                                      [((int.from_bytes(block[:8], "little") >> (4 * i)) & 15) * 17 for i in range(16)])
                            nonopaque = any(alphas[py * 4 + px] != 255 for py in range(valid_h) for px in range(valid_w))
                            if len(alpha_cache) < 65536:
                                alpha_cache[alpha_key] = nonopaque
                        discarded_alpha = nonopaque
                    payload.extend(_bc3_colour_to_bc1(block))
            output = _output_header(data, info, target_format) + payload
            if discarded_alpha:
                result["diagnostics"].append(_diagnostic("DDS_UNUSED_ALPHA_REMOVED", "opaque/MGA用途で使われないalphaを除去しました。RGBとmipmapは保持しています。", source, "warning"))
        elif target_format == "BC5" and info["format"] in {"BC1", "BC2", "BC3"}:
            payload = bytearray()
            normal_cache = {}
            normal_clamped_blocks = 0
            try:
                for mip in info["mips"]:
                    for block_index in range(mip["blocks_x"] * mip["blocks_y"]):
                        if block_index % 128 == 0:
                            _check_cancel(cancel)
                        size = _BLOCK_SIZE[info["format"]]
                        offset = mip["offset"] + block_index * size
                        bx, by = block_index % mip["blocks_x"], block_index // mip["blocks_x"]
                        if info["format"] == "BC3" and data[offset:offset + 8] != b"\xff\xff\0\0\0\0\0\0":
                            # BC3 normals usually are DXT5nm (X in alpha); only a fully opaque alpha is plain RGB.
                            raise DDSConversionError("NORMAL_LAYOUT_UNKNOWN", "BC3のnormalのalphaが使われており、DXT5nm等の可能性があります。")
                        # BC2/BC3 keep their RGB565 colour block in the last 8 bytes, like BC1.
                        colour = offset + size - 8
                        cache_key = (data[colour:colour + 8], min(4, mip["width"] - bx * 4), min(4, mip["height"] - by * 4))
                        converted = normal_cache.get(cache_key)
                        if converted is None:
                            converted = _normal_bc1_to_bc5(*cache_key)
                            if len(normal_cache) < 65536:
                                normal_cache[cache_key] = converted
                        payload.extend(converted[0])
                        normal_clamped_blocks += int(converted[1])
            except DDSConversionError as error:
                if error.code != "NORMAL_LAYOUT_UNKNOWN":
                    raise
                if normal_layout == 'rgb_with_rg_fallback':
                    # An explicit redGreen declaration is required. Packed
                    # BC3 alpha is still ambiguous and must remain untouched.
                    payload = bytearray()
                    rg_cache = {}
                    for mip in info['mips']:
                        size = _BLOCK_SIZE[info['format']]
                        for block_index in range(mip['blocks_x'] * mip['blocks_y']):
                            if block_index % 128 == 0:
                                _check_cancel(cancel)
                            offset = mip['offset'] + block_index * size
                            if info['format'] == 'BC3' and data[offset:offset + 8] != b'\xff\xff\0\0\0\0\0\0':
                                payload = None
                                break
                            bx, by = block_index % mip['blocks_x'], block_index // mip['blocks_x']
                            colour = offset + size - 8
                            try:
                                key = (data[colour:colour + 8], min(4, mip['width'] - bx * 4),
                                       min(4, mip['height'] - by * 4), info['format'] != 'BC1')
                                encoded = rg_cache.get(key)
                                if encoded is None:
                                    encoded = _declared_rg_normal(*key)
                                    if len(rg_cache) < 65536:
                                        rg_cache[key] = encoded
                                payload.extend(encoded)
                            except DDSConversionError:
                                payload = None
                                break
                        if payload is None:
                            break
                    if payload is not None:
                        output = _output_header(data, info, 'BC5') + payload
                        result['normal_layout'] = 'declared_RG_to_BC5_XY_no_green_inversion'
                        result['diagnostics'].append(_diagnostic('TEXTURE_DECLARED_RG_NORMAL',
                            'RGB法線として成立しない画像を、材質のredGreen指定に従いRG法線としてBC5へ変換しました。', source, 'info'))
                    else:
                        target_format = info['format']
                        output = data
                        result['diagnostics'].append(_diagnostic('TEXTURE_NORMAL_KEPT',
                            f'{error} RG指定でも安全に変換できないため、{target_format}のまま使います。', source, 'warning'))
                else:
                    # Undeclared layouts retain the original safe fallback.
                    target_format = info["format"]
                    output = data
                    result["diagnostics"].append(_diagnostic("TEXTURE_NORMAL_KEPT",
                        f"{error} BC5へ変換せず、{target_format}のまま使います（TPF3の推奨はBC5です）。", source, "warning"))
            else:
                output = _output_header(data, info, target_format) + payload
                result["normal_layout"] = "RGB_XYZ_to_BC5_XY_no_green_inversion"
                if normal_clamped_blocks:
                    result["diagnostics"].append(_diagnostic("NORMAL_Z_QUANTIZATION_CLAMPED", f"RGB565の量子化で0未満になった小さなnormal Zを補正しました（{normal_clamped_blocks}ブロック）。", source, "warning"))
                    result["normal_z_clamped_blocks"] = normal_clamped_blocks
        else:
            code = "NORMAL_LAYOUT_UNKNOWN" if target_format == "BC5" else "DDS_CONVERSION_UNSUPPORTED"
            raise DDSConversionError(code, f"{info['format']} → {target_format} ({role}) は自動変換できません。normalは通常RGB形式のBC1のみ変換できます。")
        output, added_mips = _complete_mip_chain(output, target_format, cancel)
        if added_mips:
            result["mip_count_after"] = 1 + added_mips
            result["diagnostics"].append(_diagnostic("TEXTURE_MIPMAPS_GENERATED",
                f"mipmapが1段しかなかったため、{1 + added_mips}段まで生成しました（縮小して{target_format}へ再圧縮。最上位の画像は変えていません）。", source, "info"))
        checked = _parse_dds(output)
        if checked["format"] != target_format or checked["mip_count"] != info["mip_count"] + added_mips:
            raise DDSConversionError("DDS_OUTPUT_INVALID", "変換後DDSの形式またはmipmap数が不正です。")
        _check_cancel(cancel)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(output)
        result.update({"changed": output != data, "format_after": target_format, "format": target_format,
                       "status": "success", "target_sha256": hashlib.sha256(output).hexdigest(),
                       "bytes_before": len(data), "bytes_after": len(output)})
    except DDSConversionError as error:
        result["diagnostics"].append(_diagnostic(error.code, str(error), source))
    return result
