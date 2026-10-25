from pathlib import Path
import struct
import tempfile
import unittest

from converter.transforms.textures import convert_dds, inspect_dds, _decode_bc1, _decode_bc4
from tests.test_textures import dds


def gradient_bc1(width, height):
    """BC1 blocks for a width x height image; every block is one flat colour that changes along x, so averages are predictable."""
    blocks = []
    for block_y in range((height + 3) // 4):
        for block_x in range((width + 3) // 4):
            red = min(31, block_x)
            blocks.append(struct.pack('<HHI', (red << 11) | 0x07E0, (red << 11) | 0x07C0, 0))
    return blocks


class MipChainTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / 'in.dds'
        self.target = self.root / 'out' / 'out.dds'

    def tearDown(self):
        self.temp.cleanup()

    def convert(self, data, role):
        self.source.write_bytes(data)
        return convert_dds(self.source, self.target, role)

    def test_a_single_level_texture_gets_the_full_chain_and_level_zero_is_untouched(self):
        """MOD 9000000012: TPF3 warns "does not have any mipmaps" for a DDS with one level; the author's DXT3 files had one."""
        blocks = gradient_bc1(64, 64)
        original = dds(b'DXT1', blocks, width=64, height=64, mip_count=1)
        result = self.convert(original, 'opaque_albedo')
        self.assertEqual(result['status'], 'success')
        info = inspect_dds(self.target)
        self.assertEqual(info['mip_count'], 7)
        self.assertEqual([(m['width'], m['height']) for m in info['mips']],
                         [(64, 64), (32, 32), (16, 16), (8, 8), (4, 4), (2, 2), (1, 1)])
        converted = self.target.read_bytes()
        top = info['mips'][0]
        self.assertEqual(converted[top['offset']:top['offset'] + top['size']], original[128:])
        self.assertIn('TEXTURE_MIPMAPS_GENERATED', {d['code'] for d in result['diagnostics']})
        flags = struct.unpack_from('<I', converted, 8)[0]
        caps = struct.unpack_from('<I', converted, 108)[0]
        self.assertTrue(flags & 0x20000 and caps & 0x400008 == 0x400008)
        self.assertEqual(self.source.read_bytes(), original)

    def test_a_smaller_level_is_the_average_of_the_level_above(self):
        flat = struct.pack('<HHI', 0xF800, 0xF7FF, 0)  # one red everywhere
        original = dds(b'DXT1', [flat] * 4, width=8, height=8, mip_count=1)
        self.convert(original, 'opaque_albedo')
        info = inspect_dds(self.target)
        converted = self.target.read_bytes()
        for mip in info['mips'][1:]:
            for texel in _decode_bc1(converted[mip['offset']:mip['offset'] + 8], True):
                self.assertEqual(texel[:3], (255, 0, 0))

    def test_a_non_square_texture_stops_at_one_by_one(self):
        original = dds(b'DXT1', [struct.pack('<HHI', 0x07E0, 0x07C0, 0)] * 2, width=8, height=4, mip_count=1)
        self.convert(original, 'opaque_albedo')
        info = inspect_dds(self.target)
        self.assertEqual([(m['width'], m['height']) for m in info['mips']], [(8, 4), (4, 2), (2, 1), (1, 1)])

    def test_normal_maps_and_transparent_albedo_are_completed_in_their_own_format(self):
        bc5 = dds(b'ATI2', [b'\x80\x80' + b'\0' * 6 + b'\x80\x80' + b'\0' * 6] * 4, width=8, height=8, mip_count=1)
        self.convert(bc5, 'normal')
        info = inspect_dds(self.target)
        self.assertEqual((info['format'], info['mip_count']), ('BC5', 4))
        converted = self.target.read_bytes()
        for mip in info['mips'][1:]:
            block = converted[mip['offset']:mip['offset'] + 16]
            self.assertTrue(all(abs(v - 128) <= 2 for v in _decode_bc4(block[:8]) + _decode_bc4(block[8:])))
        bc3 = dds(b'DXT5', [bytes((64, 128)) + b'\xAA' * 6 + struct.pack('<HHI', 0xF800, 0x001F, 0xE4E4E4E4)] * 4,
                  width=8, height=8, mip_count=1)
        self.convert(bc3, 'transparent_albedo')
        info = inspect_dds(self.target)
        self.assertEqual((info['format'], info['mip_count']), ('BC3', 4))

    def test_a_bc2_transparent_albedo_keeps_its_format_and_a_flat_alpha(self):
        """The author's DXT3 files of MOD 9000000012 (kept as BC2 for transparent_albedo)."""
        alpha = (0x99999999 | (0x99999999 << 32)).to_bytes(8, 'little')  # every texel alpha 0x99 (nibble 9)
        block = alpha + struct.pack('<HHI', 0xF800, 0xF7FF, 0)
        self.convert(dds(b'DXT3', [block] * 4, width=8, height=8, mip_count=1), 'transparent_albedo')
        info = inspect_dds(self.target)
        self.assertEqual((info['format'], info['mip_count']), ('BC2', 4))
        converted = self.target.read_bytes()
        for mip in info['mips']:
            self.assertEqual(converted[mip['offset']:mip['offset'] + 8], alpha)

    def test_a_dds_that_no_slot_converted_is_completed_in_the_package(self):
        """Roll-sign textures (MOD 9000000012 roll_211_loc.dds) are referenced by materials but copied as they are."""
        from converter.transforms.texture_usage import convert_package_textures
        content = self.root / 'package' / 'content' / '_assets' / 'texture'
        content.mkdir(parents=True)
        block = bytes([255]) * 8 + struct.pack('<HHI', 0xF800, 0xF7FF, 0)
        path = content / 'roll.dds'
        path.write_bytes(dds(b'DXT3', [block] * 4, width=8, height=8, mip_count=1))
        diagnostics = convert_package_textures(self.root / 'package')
        self.assertEqual(inspect_dds(path)['mip_count'], 4)
        self.assertEqual([d['code'] for d in diagnostics], ['TEXTURE_MIPMAPS_GENERATED'])
        self.assertEqual(convert_package_textures(self.root / 'package'), [])  # complete now: nothing to do

    def test_a_complete_chain_and_an_authors_partial_chain_are_left_alone(self):
        blocks = [struct.pack('<HHI', 0x07E0, 0x07C0, 0)]
        complete = dds(b'DXT1', blocks)
        self.convert(complete, 'opaque_albedo')
        self.assertEqual(self.target.read_bytes(), complete)
        partial = dds(b'DXT1', blocks * 2, mip_count=2)
        self.convert(partial, 'opaque_albedo')
        self.assertEqual(self.target.read_bytes(), partial)

    def test_a_bc3_with_one_level_converted_to_bc1_is_completed_too(self):
        block = b'\xff\xff' + b'\0' * 6 + struct.pack('<HHI', 0xF800, 0x001F, 0xE4E4E4E4)
        result = self.convert(dds(b'DXT5', [block] * 4, width=8, height=8, mip_count=1), 'opaque_albedo')
        info = inspect_dds(self.target)
        self.assertEqual((result['format_after'], info['format'], info['mip_count']), ('BC1', 'BC1', 4))


if __name__ == '__main__':
    unittest.main()
