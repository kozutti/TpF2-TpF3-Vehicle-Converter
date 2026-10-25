import struct
import tempfile
import unittest
from pathlib import Path

from converter.transforms import textures


def raw_dds(width, height, mips, pixel, dx10=None):
    """Uncompressed 32-bit BGRA DDS (legacy masks) or DX10 B8G8R8A8; pixel(x, y, w, h) -> (r, g, b, a)."""
    header = bytearray(128)
    header[:4] = b"DDS "
    struct.pack_into("<I", header, 4, 124)
    struct.pack_into("<I", header, 8, 0x1 | 0x2 | 0x4 | 0x1000 | 0x20000 | 0x8)
    struct.pack_into("<5I", header, 12, height, width, width * 4, 1, mips)
    struct.pack_into("<I", header, 76, 32)
    struct.pack_into("<I", header, 80, 0x41)
    struct.pack_into("<I", header, 88, 32)
    struct.pack_into("<4I", header, 92, 0xFF0000, 0xFF00, 0xFF, 0xFF000000)
    struct.pack_into("<I", header, 108, 0x1000 | 8 | 0x400000)
    body = bytearray()
    mip_width, mip_height = width, height
    for _ in range(mips):
        for y in range(mip_height):
            for x in range(mip_width):
                r, g, b, a = pixel(x, y, mip_width, mip_height)
                body += bytes((b, g, r, a))
        mip_width, mip_height = max(1, mip_width // 2), max(1, mip_height // 2)
    if dx10:
        struct.pack_into("<I", header, 80, 0x4)
        header[84:88] = b"DX10"
        header[88:108] = b"\0" * 20
        header += struct.pack("<5I", dx10, 3, 0, 1, 0)
    return bytes(header) + bytes(body)


def normal_pixel(x, y, w, h):
    return (128 + int(100 * x / w) - 50, 128 + int(100 * y / h) - 50, 220, 255)


class UncompressedDdsTests(unittest.TestCase):
    def convert(self, data, role, name="src.dds"):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        source = Path(directory.name) / name
        source.write_bytes(data)
        target = Path(directory.name) / "out" / name
        result = textures.convert_dds(source, target, role)
        return result, target

    def test_uncompressed_normal_becomes_bc5_with_every_mip(self):
        result, target = self.convert(raw_dds(16, 16, 5, normal_pixel), "normal")
        self.assertEqual(result["status"], "success", result["diagnostics"])
        self.assertEqual((result["format_before"], result["format_after"]), ("RAW", "BC5"))
        info = textures.inspect_dds(target)
        self.assertEqual((info["format"], info["mip_count"], info["width"]), ("BC5", 5, 16))
        first = target.read_bytes()[128:144]
        red = textures._decode_bc4(first[:8])[:4]
        self.assertTrue(all(abs(a - normal_pixel(x, 0, 16, 16)[0]) <= 2 for x, a in enumerate(red)), red)
        self.assertIn("TEXTURE_UNCOMPRESSED_ENCODED", {d["code"] for d in result["diagnostics"]})

    def test_opaque_albedo_becomes_bc1_and_flags_dropped_alpha(self):
        result, target = self.convert(raw_dds(8, 8, 4, lambda x, y, w, h: (200, 100, 50, 128)), "opaque_albedo")
        self.assertEqual(result["status"], "success", result["diagnostics"])
        self.assertEqual(textures.inspect_dds(target)["format"], "BC1")
        colour = textures._decode_bc1(target.read_bytes()[128:136])[0]
        self.assertTrue(abs(colour[0] - 200) <= 8 and abs(colour[1] - 100) <= 4 and abs(colour[2] - 50) <= 8, colour)
        self.assertIn("DDS_UNUSED_ALPHA_REMOVED", {d["code"] for d in result["diagnostics"]})

    def test_transparent_albedo_keeps_alpha_as_bc3_and_opaque_one_becomes_bc1(self):
        result, target = self.convert(raw_dds(8, 8, 1, lambda x, y, w, h: (10, 20, 30, x * 30)), "transparent_albedo")
        self.assertEqual(textures.inspect_dds(target)["format"], "BC3")
        result, target = self.convert(raw_dds(8, 8, 1, lambda x, y, w, h: (10, 20, 30, 255)), "transparent_albedo")
        self.assertEqual(textures.inspect_dds(target)["format"], "BC1")

    def test_dx10_header_and_odd_sizes(self):
        result, target = self.convert(raw_dds(6, 3, 3, normal_pixel, dx10=87), "normal")
        self.assertEqual(result["status"], "success", result["diagnostics"])
        info = textures.inspect_dds(target)
        self.assertEqual((info["format"], info["mip_count"], info["dxgi_format"]), ("BC5", 3, 83))

    def test_truncated_or_unusual_raw_layout_is_refused_not_guessed(self):
        data = raw_dds(8, 8, 4, normal_pixel)
        result, target = self.convert(data[:-5], "normal")
        self.assertEqual(result["status"], "manual")
        self.assertFalse(target.exists())
        odd = bytearray(data)
        struct.pack_into("<4I", odd, 92, 0xF00, 0xF0, 0xF, 0xF000)  # 4 bit channels
        result, _ = self.convert(bytes(odd), "normal")
        self.assertEqual(result["status"], "manual")
        self.assertIn("DDS_FORMAT_UNSUPPORTED", {d["code"] for d in result["diagnostics"]})


if __name__ == "__main__":
    unittest.main()
