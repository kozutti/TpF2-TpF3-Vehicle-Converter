import unittest

from converter.tpf3_sounds import SHARED_SOUNDS, stock_audio_for


class StockAudioTests(unittest.TestCase):
    def test_the_kind_comes_from_the_file_name_and_the_family_from_the_engines(self):
        cases = [
            ('vehicle/own/Horn_1.wav', {'DIESEL'}, '::/vehicle/train/shared/sound/train_diesel/horn_23_Diesel.wav'),
            ('vehicle/own/horn.wav', {'ELECTRIC'}, '::/vehicle/train/shared/sound/train_electric_modern/horn_1.wav'),
            ('x/brake_loop.wav', set(), '::/vehicle/train/shared/sound/train/brakes.wav'),
            ('x/curve_squeal.wav', {'DIESEL'}, '::/vehicle/train/shared/sound/train_diesel/wheels_ringing.wav'),
            ('x/clack_1.wav', {'ELECTRIC'}, '::/vehicle/train/shared/sound/clack/modern/part_1.wav'),
            ('x/idle.wav', {'DIESEL'}, '::/vehicle/train/shared/sound/train_diesel_electric/train_diesel_electric_idle.wav'),
            ('x/note01.wav', {'STEAM'}, '::/vehicle/train/shared/sound/train_steam_old/chuff_1.wav'),
            ('x/whatever.wav', {'ELECTRIC'}, '::/vehicle/train/shared/sound/train_electric_modern/drive.wav'),
        ]
        for reference, engines, expected in cases:
            with self.subTest(reference=reference, engines=engines):
                self.assertEqual(stock_audio_for(reference, engines), expected)

    def test_every_stand_in_is_a_file_tpf3_ships(self):
        for engines in (set(), {'ELECTRIC'}, {'DIESEL'}, {'STEAM'}):
            for name in ('horn', 'brake', 'ringing', 'clack', 'idle', 'drive', 'unknown'):
                self.assertIn(stock_audio_for(f'x/{name}.wav', engines)[3:], SHARED_SOUNDS)


if __name__ == '__main__':
    unittest.main()
