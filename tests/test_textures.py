from pathlib import Path
import struct
import tempfile
import threading
import unittest

from converter.transforms.textures import (
    DDSConversionError, convert_dds, inspect_dds,
    _decode_bc1, _decode_bc4,
)


def dds(fourcc, blocks, width=4, height=4, mip_count=None, dxgi=None):
    """A DDS whose mip chain is complete unless `mip_count` says otherwise (a 4x4 level is one block, so the blocks repeat).
    The converter adds the missing levels of a single-level DDS, which these byte-for-byte tests do not want."""
    if mip_count is None:
        assert (width, height) == (4, 4)
        mip_count = 3
        blocks = list(blocks) * mip_count
    header = bytearray(128 if dxgi is None else 148)
    header[:4] = b"DDS "
    struct.pack_into("<I", header, 4, 124)
    struct.pack_into("<I", header, 8, 0xA1007)
    struct.pack_into("<I", header, 12, height)
    struct.pack_into("<I", header, 16, width)
    struct.pack_into("<I", header, 20, len(blocks[0]))
    struct.pack_into("<I", header, 28, mip_count)
    struct.pack_into("<I", header, 76, 32)
    struct.pack_into("<I", header, 80, 4)
    header[84:88] = fourcc if dxgi is None else b"DX10"
    struct.pack_into("<I", header, 108, 0x401008 if mip_count > 1 else 0x1000)
    if dxgi is not None:
        struct.pack_into("<5I", header, 128, dxgi, 3, 0, 1, 0)
    return bytes(header) + b"".join(blocks)


class TextureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / "入力 日本語.dds"
        self.target = self.root / "output" / "converted.dds"

    def tearDown(self):
        self.temp.cleanup()

    def test_bc3_rgb_lossless_even_when_endpoints_need_swapping(self):
        # Four unique palette entries, c0<c1: BC3 supports four colours but
        # blindly stripping its alpha block would give BC1 transparent mode.
        colour = struct.pack("<HHI", 0x001F, 0xF800, 0xE4E4E4E4)
        block = bytes((17, 17)) + b"\0" * 6 + colour
        original = dds(b"DXT5", [block] * 3, mip_count=3)
        self.source.write_bytes(original)
        result = convert_dds(self.source, self.target, "opaque_albedo")
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["format_after"], "BC1")
        self.assertEqual(result["mip_count"], 3)
        self.assertEqual(self.source.read_bytes(), original)
        self.assertTrue(any(d["code"] == "DDS_UNUSED_ALPHA_REMOVED" for d in result["diagnostics"]))
        converted = self.target.read_bytes()
        info = inspect_dds(self.target)
        self.assertEqual([(m["width"], m["height"]) for m in info["mips"]], [(4, 4), (2, 2), (1, 1)])
        for mip in info["mips"]:
            self.assertEqual(_decode_bc1(converted[mip["offset"]:mip["offset"] + 8]), _decode_bc1(colour, True))

    def test_equal_bc3_endpoints_do_not_create_transparency(self):
        colour = struct.pack("<HHI", 0, 0, 0xFFFFFFFF)
        self.source.write_bytes(dds(b"DXT5", [b"\xff\xff" + b"\0" * 6 + colour]))
        result = convert_dds(self.source, self.target, "metal_gloss_ao")
        self.assertEqual(result["status"], "success")
        self.assertEqual(_decode_bc1(self.target.read_bytes()[128:136]), [(0, 0, 0, 255)] * 16)

    def test_normal_reconstructs_xyz_then_stores_xy_without_green_flip(self):
        # Unit normal about (0.30, -0.41, 0.86), rather than flat normal.
        endpoint = (20 << 11) | (19 << 5) | 29
        colour = struct.pack("<HHI", endpoint, endpoint - 1, 0)
        self.source.write_bytes(dds(b"DXT1", [colour] * 3, mip_count=3))
        result = convert_dds(self.source, self.target, "normal")
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["format_after"], "BC5")
        info = inspect_dds(self.target)
        self.assertEqual(info["mip_count"], 3)
        self.assertEqual(info["fourcc"], "ATI2")
        payload = self.target.read_bytes()
        # XY bytes are now independently BC4-compressed, not BC1 colour bytes.
        for mip in info["mips"]:
            block = payload[mip["offset"]:mip["offset"] + 16]
            red, green = _decode_bc4(block[:8]), _decode_bc4(block[8:])
            self.assertTrue(all(160 <= pixel <= 170 for pixel in red))
            self.assertTrue(all(70 <= pixel <= 82 for pixel in green))

    def test_bc3_normal_with_opaque_alpha_is_plain_rgb_and_converted(self):
        self.source.write_bytes(dds(b"DXT5", [b"\xff\xff" + b"\0" * 6 + struct.pack("<HHI", 0x841F, 0x841E, 0)]))
        result = convert_dds(self.source, self.target, "normal")
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["format_after"], "BC5")
        self.assertEqual([d["code"] for d in result["diagnostics"]], [])

    def test_bc3_normal_with_used_alpha_is_kept_and_bc2_rgb_normal_is_converted(self):
        colour = struct.pack("<HHI", 0x841F, 0x841F, 0)  # a flat normal (0.0, 0.0, 1.0) in RGB565
        self.source.write_bytes(dds(b"DXT5", [bytes((200, 10)) + b"\0" * 6 + colour]))
        result = convert_dds(self.source, self.target, "normal")
        self.assertEqual(result["format_after"], "BC3")
        self.assertEqual([d["code"] for d in result["diagnostics"]], ["TEXTURE_NORMAL_KEPT"])
        self.source.write_bytes(dds(b"DXT3", [b"\xff" * 8 + colour]))
        result = convert_dds(self.source, self.target, "normal")
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["format_after"], "BC5")
        self.assertEqual(inspect_dds(self.target)["format"], "BC5")

    def test_negative_normal_z_keeps_the_texture_and_warns(self):
        # Not plain RGB XYZ, so a BC5 copy could be wrong. TPF3 only recommends BC5: keep the data, say so.
        self.source.write_bytes(dds(b"DXT1", [struct.pack("<HHI", 0x8400, 0x8300, 0)]))
        result = convert_dds(self.source, self.target, "normal")
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["format_after"], "BC1")
        self.assertEqual(self.target.read_bytes(), self.source.read_bytes())
        self.assertEqual([(d["code"], d["severity"]) for d in result["diagnostics"]], [("TEXTURE_NORMAL_KEPT", "warning")])

    def test_small_negative_z_from_rgb565_quantization_is_reported(self):
        # Blue 5-bit value 15 decodes to 123: very slightly below tangent Z=0.
        endpoint = (9 << 11) | (2 << 5) | 15
        self.source.write_bytes(dds(b"DXT1", [struct.pack("<HHI", endpoint, endpoint - 1, 0)]))
        result = convert_dds(self.source, self.target, "normal")
        self.assertEqual(result["status"], "success")
        self.assertTrue(any(d["code"] == "NORMAL_Z_QUANTIZATION_CLAMPED" for d in result["diagnostics"]))

    def test_transparent_bc1_and_bc7_are_kept_without_loss(self):
        for fourcc, dxgi, block in ((b"DXT1", None, struct.pack("<HHI", 0xF800, 0x001F, 0)),
                                    (b"DX10", 98, b"\0" * 16)):
            original = dds(fourcc, [block], dxgi=dxgi)
            self.source.write_bytes(original)
            result = convert_dds(self.source, self.target, "transparent_albedo")
            self.assertEqual(result["status"], "success")
            self.assertEqual(self.target.read_bytes(), original)

    def test_transparent_bc3_preserves_every_byte_and_alpha(self):
        original = dds(b"DXT5", [bytes((64, 128)) + b"\xAA" * 6 + struct.pack("<HHI", 0xF800, 0x001F, 0xE4E4E4E4)])
        self.source.write_bytes(original)
        result = convert_dds(self.source, self.target, "transparent_albedo")
        self.assertFalse(result["changed"])
        self.assertEqual(self.target.read_bytes(), original)

    def test_already_correct_texture_is_still_copied(self):
        original = dds(b"ATI2", [b"\x80\x80" + b"\0" * 6 + b"\x80\x80" + b"\0" * 6])
        self.source.write_bytes(original)
        result = convert_dds(self.source, self.target, "normal")
        self.assertFalse(result["changed"])
        self.assertEqual(result["status"], "success")
        self.assertEqual(self.target.read_bytes(), original)

    def test_dx10_srgb_colour_tag_is_preserved(self):
        block = b"\xff\xff" + b"\0" * 6 + struct.pack("<HHI", 0xF800, 0x001F, 0)
        self.source.write_bytes(dds(b"DXT5", [block], dxgi=78))
        result = convert_dds(self.source, self.target, "metal_gloss_ao")
        self.assertEqual(result["status"], "success")
        self.assertEqual(inspect_dds(self.target)["dxgi_format"], 72)

    def test_truncated_mipmap_and_in_place_are_not_written(self):
        original = dds(b"DXT1", [b"\0" * 8], mip_count=2)
        self.source.write_bytes(original)
        with self.assertRaises(DDSConversionError):
            inspect_dds(self.source)
        result = convert_dds(self.source, self.source, "normal")
        self.assertEqual(result["diagnostics"][0]["code"], "DDS_IN_PLACE_FORBIDDEN")
        self.assertEqual(self.source.read_bytes(), original)

    def test_cancel_does_not_create_target(self):
        self.source.write_bytes(dds(b"DXT1", [struct.pack("<HHI", 0x841F, 0x841E, 0)]))
        cancel = threading.Event()
        cancel.set()
        with self.assertRaises(InterruptedError):
            convert_dds(self.source, self.target, "normal", cancel=cancel)
        self.assertFalse(self.target.exists())


if __name__ == "__main__":
    unittest.main()
