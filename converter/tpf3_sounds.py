"""Names (not contents) of the audio files TPF3 ships for rail vehicles, to map TPF2 stock sounds to them.

Generated from the TPF3 install (base/content/vehicle/{train,waggon,tram}/shared/sound). TPF2 sound sets name
their stock audio as "vehicle/<X>"; TPF3 keeps the same files at "::/vehicle/<category>/shared/sound/<X>".
"""

SHARED_SOUNDS = frozenset({
    'vehicle/train/shared/sound/clack/modern/part_1.wav',
    'vehicle/train/shared/sound/clack/modern/part_10.wav',
    'vehicle/train/shared/sound/clack/modern/part_2.wav',
    'vehicle/train/shared/sound/clack/modern/part_3.wav',
    'vehicle/train/shared/sound/clack/modern/part_4.wav',
    'vehicle/train/shared/sound/clack/modern/part_5.wav',
    'vehicle/train/shared/sound/clack/modern/part_6.wav',
    'vehicle/train/shared/sound/clack/modern/part_7.wav',
    'vehicle/train/shared/sound/clack/modern/part_8.wav',
    'vehicle/train/shared/sound/clack/modern/part_9.wav',
    'vehicle/train/shared/sound/clack/old/part_1.wav',
    'vehicle/train/shared/sound/clack/old/part_10.wav',
    'vehicle/train/shared/sound/clack/old/part_2.wav',
    'vehicle/train/shared/sound/clack/old/part_3.wav',
    'vehicle/train/shared/sound/clack/old/part_4.wav',
    'vehicle/train/shared/sound/clack/old/part_5.wav',
    'vehicle/train/shared/sound/clack/old/part_6.wav',
    'vehicle/train/shared/sound/clack/old/part_7.wav',
    'vehicle/train/shared/sound/clack/old/part_8.wav',
    'vehicle/train/shared/sound/clack/old/part_9.wav',
    'vehicle/train/shared/sound/train/brakes.wav',
    'vehicle/train/shared/sound/train/wheels_ringing1.wav',
    'vehicle/train/shared/sound/train/wheels_ringing2.wav',
    'vehicle/train/shared/sound/train_diesel/_brakes.wav',
    'vehicle/train/shared/sound/train_diesel/_wheels_ringing.wav',
    'vehicle/train/shared/sound/train_diesel/brakes.wav',
    'vehicle/train/shared/sound/train_diesel/horn_13_freight.wav',
    'vehicle/train/shared/sound/train_diesel/horn_23_Diesel.wav',
    'vehicle/train/shared/sound/train_diesel/horn_23_Diesel_old.wav',
    'vehicle/train/shared/sound/train_diesel/horn_24_Diesel.wav',
    'vehicle/train/shared/sound/train_diesel/idle_drive.wav',
    'vehicle/train/shared/sound/train_diesel/wheels_ringing.wav',
    'vehicle/train/shared/sound/train_diesel_electric/train_diesel_electric_drive.wav',
    'vehicle/train/shared/sound/train_diesel_electric/train_diesel_electric_idle.wav',
    'vehicle/train/shared/sound/train_diesel_electric/train_diesel_electric_motor.wav',
    'vehicle/train/shared/sound/train_diesel_hydraulic/train_diesel_hydraulic_accelerate.wav',
    'vehicle/train/shared/sound/train_diesel_hydraulic/train_diesel_hydraulic_drive.wav',
    'vehicle/train/shared/sound/train_diesel_hydraulic/train_diesel_hydraulic_idle.wav',
    'vehicle/train/shared/sound/train_diesel_old/train_diesel_old_accelerate.wav',
    'vehicle/train/shared/sound/train_diesel_old/train_diesel_old_drive.wav',
    'vehicle/train/shared/sound/train_diesel_old/train_diesel_old_idle.wav',
    'vehicle/train/shared/sound/train_electric_medium/train_electric_medium_accelerate.wav',
    'vehicle/train/shared/sound/train_electric_medium/train_electric_medium_drive.wav',
    'vehicle/train/shared/sound/train_electric_medium/train_electric_medium_power.wav',
    'vehicle/train/shared/sound/train_electric_medium/train_electric_medium_shift.wav',
    'vehicle/train/shared/sound/train_electric_modern/_brakes.wav',
    'vehicle/train/shared/sound/train_electric_modern/_wheels_ringing.wav',
    'vehicle/train/shared/sound/train_electric_modern/brakes-imported.wav',
    'vehicle/train/shared/sound/train_electric_modern/drive-imported.wav',
    'vehicle/train/shared/sound/train_electric_modern/drive.wav',
    'vehicle/train/shared/sound/train_electric_modern/horn_1.wav',
    'vehicle/train/shared/sound/train_electric_modern/horn_10.wav',
    'vehicle/train/shared/sound/train_electric_modern/horn_11.wav',
    'vehicle/train/shared/sound/train_electric_modern/horn_12.wav',
    'vehicle/train/shared/sound/train_electric_modern/horn_14.wav',
    'vehicle/train/shared/sound/train_electric_modern/horn_15.wav',
    'vehicle/train/shared/sound/train_electric_modern/horn_16.wav',
    'vehicle/train/shared/sound/train_electric_modern/horn_2.wav',
    'vehicle/train/shared/sound/train_electric_modern/horn_21_intercity125.wav',
    'vehicle/train/shared/sound/train_electric_modern/horn_3.wav',
    'vehicle/train/shared/sound/train_electric_modern/horn_4.wav',
    'vehicle/train/shared/sound/train_electric_modern/horn_5.wav',
    'vehicle/train/shared/sound/train_electric_modern/horn_6.wav',
    'vehicle/train/shared/sound/train_electric_modern/horn_7.wav',
    'vehicle/train/shared/sound/train_electric_modern/horn_8.wav',
    'vehicle/train/shared/sound/train_electric_modern/horn_9.wav',
    'vehicle/train/shared/sound/train_electric_modern/train_electric_modern_accelerate.wav',
    'vehicle/train/shared/sound/train_electric_modern/train_electric_modern_drive.wav',
    'vehicle/train/shared/sound/train_electric_modern/train_electric_modern_idle.wav',
    'vehicle/train/shared/sound/train_electric_modern/wheels_ringing-imported.wav',
    'vehicle/train/shared/sound/train_electric_old/_brakes.wav',
    'vehicle/train/shared/sound/train_electric_old/_wheels_ringing.wav',
    'vehicle/train/shared/sound/train_electric_old/brakes-imported-001.wav',
    'vehicle/train/shared/sound/train_electric_old/drive-imported-001.wav',
    'vehicle/train/shared/sound/train_electric_old/drive.wav',
    'vehicle/train/shared/sound/train_electric_old/train_electric_old_accelerate.wav',
    'vehicle/train/shared/sound/train_electric_old/train_electric_old_drive.wav',
    'vehicle/train/shared/sound/train_electric_old/train_electric_old_power.wav',
    'vehicle/train/shared/sound/train_electric_old/train_electric_old_shift.wav',
    'vehicle/train/shared/sound/train_electric_old/wheels_ringing-imported-001.wav',
    'vehicle/train/shared/sound/train_electric_old/whistle.wav',
    'vehicle/train/shared/sound/train_electric_old/whistle2.wav',
    'vehicle/train/shared/sound/train_steam_modern/_brakes.wav',
    'vehicle/train/shared/sound/train_steam_modern/_wheels_ringing.wav',
    'vehicle/train/shared/sound/train_steam_modern/brakes-imported-002.wav',
    'vehicle/train/shared/sound/train_steam_modern/steam_new_chuff_1.wav',
    'vehicle/train/shared/sound/train_steam_modern/steam_new_chuff_10.wav',
    'vehicle/train/shared/sound/train_steam_modern/steam_new_chuff_2.wav',
    'vehicle/train/shared/sound/train_steam_modern/steam_new_chuff_3.wav',
    'vehicle/train/shared/sound/train_steam_modern/steam_new_chuff_4.wav',
    'vehicle/train/shared/sound/train_steam_modern/steam_new_chuff_5.wav',
    'vehicle/train/shared/sound/train_steam_modern/steam_new_chuff_6.wav',
    'vehicle/train/shared/sound/train_steam_modern/steam_new_chuff_7.wav',
    'vehicle/train/shared/sound/train_steam_modern/steam_new_chuff_8.wav',
    'vehicle/train/shared/sound/train_steam_modern/steam_new_chuff_9.wav',
    'vehicle/train/shared/sound/train_steam_modern/steam_new_fast.wav',
    'vehicle/train/shared/sound/train_steam_modern/steam_new_horn.wav',
    'vehicle/train/shared/sound/train_steam_modern/steam_new_idle.wav',
    'vehicle/train/shared/sound/train_steam_modern/wheels_ringing-imported.wav',
    'vehicle/train/shared/sound/train_steam_old/_brakes.wav',
    'vehicle/train/shared/sound/train_steam_old/_wheels_ringing.wav',
    'vehicle/train/shared/sound/train_steam_old/brakes-imported-003.wav',
    'vehicle/train/shared/sound/train_steam_old/chuff_1.wav',
    'vehicle/train/shared/sound/train_steam_old/chuff_10.wav',
    'vehicle/train/shared/sound/train_steam_old/chuff_2.wav',
    'vehicle/train/shared/sound/train_steam_old/chuff_3.wav',
    'vehicle/train/shared/sound/train_steam_old/chuff_4.wav',
    'vehicle/train/shared/sound/train_steam_old/chuff_5.wav',
    'vehicle/train/shared/sound/train_steam_old/chuff_6.wav',
    'vehicle/train/shared/sound/train_steam_old/chuff_7.wav',
    'vehicle/train/shared/sound/train_steam_old/chuff_8.wav',
    'vehicle/train/shared/sound/train_steam_old/chuff_9.wav',
    'vehicle/train/shared/sound/train_steam_old/fast.wav',
    'vehicle/train/shared/sound/train_steam_old/horn.wav',
    'vehicle/train/shared/sound/train_steam_old/idle.wav',
    'vehicle/train/shared/sound/train_steam_old/steam_horn_1.wav',
    'vehicle/train/shared/sound/train_steam_old/steam_horn_10.wav',
    'vehicle/train/shared/sound/train_steam_old/steam_horn_11.wav',
    'vehicle/train/shared/sound/train_steam_old/steam_horn_12.wav',
    'vehicle/train/shared/sound/train_steam_old/steam_horn_13.wav',
    'vehicle/train/shared/sound/train_steam_old/steam_horn_14.wav',
    'vehicle/train/shared/sound/train_steam_old/steam_horn_15.wav',
    'vehicle/train/shared/sound/train_steam_old/steam_horn_16.wav',
    'vehicle/train/shared/sound/train_steam_old/steam_horn_17.wav',
    'vehicle/train/shared/sound/train_steam_old/steam_horn_18.wav',
    'vehicle/train/shared/sound/train_steam_old/steam_horn_19.wav',
    'vehicle/train/shared/sound/train_steam_old/steam_horn_2.wav',
    'vehicle/train/shared/sound/train_steam_old/steam_horn_20.wav',
    'vehicle/train/shared/sound/train_steam_old/steam_horn_21.wav',
    'vehicle/train/shared/sound/train_steam_old/steam_horn_22.wav',
    'vehicle/train/shared/sound/train_steam_old/steam_horn_23.wav',
    'vehicle/train/shared/sound/train_steam_old/steam_horn_24.wav',
    'vehicle/train/shared/sound/train_steam_old/steam_horn_3.wav',
    'vehicle/train/shared/sound/train_steam_old/steam_horn_4.wav',
    'vehicle/train/shared/sound/train_steam_old/steam_horn_5.wav',
    'vehicle/train/shared/sound/train_steam_old/steam_horn_6.wav',
    'vehicle/train/shared/sound/train_steam_old/steam_horn_7.wav',
    'vehicle/train/shared/sound/train_steam_old/steam_horn_8.wav',
    'vehicle/train/shared/sound/train_steam_old/steam_horn_9.wav',
    'vehicle/train/shared/sound/train_steam_old/wheels_ringing-imported-001.wav',
    'vehicle/tram/shared/sound/carriage/coach_horn_01.wav',
    'vehicle/tram/shared/sound/carriage/coach_horn_02.wav',
    'vehicle/tram/shared/sound/carriage/coach_horn_03.wav',
    'vehicle/tram/shared/sound/carriage/drive.wav',
    'vehicle/tram/shared/sound/tram_modern/bell.wav',
    'vehicle/tram/shared/sound/tram_modern/drive-imported-002.wav',
    'vehicle/tram/shared/sound/tram_modern/drive.wav',
    'vehicle/tram/shared/sound/tram_modern/idle-imported.wav',
    'vehicle/tram/shared/sound/tram_modern/idle.wav',
    'vehicle/tram/shared/sound/tram_old/bell-imported.wav',
    'vehicle/tram/shared/sound/tram_old/bell.wav',
    'vehicle/tram/shared/sound/tram_old/bell2.wav',
    'vehicle/tram/shared/sound/tram_old/bell3.wav',
    'vehicle/tram/shared/sound/tram_old/drive-imported-003.wav',
    'vehicle/tram/shared/sound/tram_old/drive.wav',
    'vehicle/tram/shared/sound/tram_steam/chuffs.wav',
    'vehicle/waggon/shared/sound/waggon_freight_modern/_train-wheels-ringing-curve.wav',
    'vehicle/waggon/shared/sound/waggon_freight_modern/_waggon_freight_modern.wav',
    'vehicle/waggon/shared/sound/waggon_freight_modern/_waggon_freight_modern_doorclose.wav',
    'vehicle/waggon/shared/sound/waggon_freight_modern/_waggon_freight_modern_dooropen.wav',
    'vehicle/waggon/shared/sound/waggon_freight_modern/drive-imported-005.wav',
    'vehicle/waggon/shared/sound/waggon_freight_modern/drive.wav',
    'vehicle/waggon/shared/sound/waggon_freight_modern/train-wheels-ringing-curve.wav',
    'vehicle/waggon/shared/sound/waggon_freight_modern/waggon_freight_modern.wav',
    'vehicle/waggon/shared/sound/waggon_freight_modern/waggon_freight_modern_doorclose.wav',
    'vehicle/waggon/shared/sound/waggon_freight_modern/waggon_freight_modern_dooropen.wav',
    'vehicle/waggon/shared/sound/waggon_freight_old/_train-wheels-ringing-curve.wav',
    'vehicle/waggon/shared/sound/waggon_freight_old/_waggon_freight_old_doorclose.wav',
    'vehicle/waggon/shared/sound/waggon_freight_old/_waggon_freight_old_dooropen.wav',
    'vehicle/waggon/shared/sound/waggon_freight_old/drive-imported-006.wav',
    'vehicle/waggon/shared/sound/waggon_freight_old/drive.wav',
    'vehicle/waggon/shared/sound/waggon_freight_old/train-wheels-ringing-curve-imported.wav',
    'vehicle/waggon/shared/sound/waggon_freight_old/waggon_freight_old_doorclose.wav',
    'vehicle/waggon/shared/sound/waggon_freight_old/waggon_freight_old_dooropen.wav',
    'vehicle/waggon/shared/sound/waggon_modern/_waggon_modern.wav',
    'vehicle/waggon/shared/sound/waggon_modern/_waggon_modern_closedoor.wav',
    'vehicle/waggon/shared/sound/waggon_modern/_waggon_modern_opendoor.wav',
    'vehicle/waggon/shared/sound/waggon_modern/_wheels_ringing.wav',
    'vehicle/waggon/shared/sound/waggon_modern/waggon_modern.wav',
    'vehicle/waggon/shared/sound/waggon_modern/waggon_modern_closedoor.wav',
    'vehicle/waggon/shared/sound/waggon_modern/waggon_modern_opendoor.wav',
    'vehicle/waggon/shared/sound/waggon_modern/wheels_ringing-imported-002.wav',
    'vehicle/waggon/shared/sound/waggon_old/_waggon_old.wav',
    'vehicle/waggon/shared/sound/waggon_old/_waggon_old_closedoor.wav',
    'vehicle/waggon/shared/sound/waggon_old/_waggon_old_opendoor.wav',
    'vehicle/waggon/shared/sound/waggon_old/_wheels_ringing.wav',
    'vehicle/waggon/shared/sound/waggon_old/waggon_old.wav',
    'vehicle/waggon/shared/sound/waggon_old/waggon_old_closedoor.wav',
    'vehicle/waggon/shared/sound/waggon_old/waggon_old_opendoor.wav',
    'vehicle/waggon/shared/sound/waggon_old/wheels_ringing-imported-003.wav',
})


def tpf3_shared_sound(reference):
    """"::/..." resource name of the TPF3 file that matches a TPF2 stock audio reference, or None."""
    name = reference.replace("\\", "/").lstrip("/")
    if name.lower().startswith("res/audio/effects/"):
        name = name[len("res/audio/effects/"):]
    if not name.lower().startswith("vehicle/"):
        return None
    rest = name[len("vehicle/"):]
    for category in ("train", "waggon", "tram"):
        candidate = f"vehicle/{category}/shared/sound/{rest}"
        if candidate in SHARED_SOUNDS:
            return "::/" + candidate
    return None


def default_horn(engine_types):
    """An existing TPF3 stock horn, when the source custom audio is absent."""
    if 'STEAM' in engine_types:
        sound = 'vehicle/train/shared/sound/train_steam_old/steam_horn_1.wav'
    elif 'DIESEL' in engine_types:
        sound = 'vehicle/train/shared/sound/train_diesel/horn_23_Diesel.wav'
    else:
        sound = 'vehicle/train/shared/sound/train_electric_modern/horn_1.wav'
    assert sound in SHARED_SOUNDS
    return '::/' + sound


_STOCK_BY_FAMILY = {
    'electric': {'drive': 'vehicle/train/shared/sound/train_electric_modern/drive.wav',
                 'idle': 'vehicle/train/shared/sound/train_electric_modern/drive.wav',
                 'brake': 'vehicle/train/shared/sound/train/brakes.wav',
                 'wheels': 'vehicle/train/shared/sound/train/wheels_ringing1.wav'},
    'diesel': {'drive': 'vehicle/train/shared/sound/train_diesel_electric/train_diesel_electric_drive.wav',
               'idle': 'vehicle/train/shared/sound/train_diesel_electric/train_diesel_electric_idle.wav',
               'brake': 'vehicle/train/shared/sound/train_diesel/brakes.wav',
               'wheels': 'vehicle/train/shared/sound/train_diesel/wheels_ringing.wav'},
    'steam': {'drive': 'vehicle/train/shared/sound/train_steam_old/chuff_1.wav',
              'idle': 'vehicle/train/shared/sound/train_steam_old/chuff_1.wav',
              'brake': 'vehicle/train/shared/sound/train/brakes.wav',
              'wheels': 'vehicle/train/shared/sound/train/wheels_ringing1.wav'},
}
_CLACK = 'vehicle/train/shared/sound/clack/modern/part_1.wav'
assert all(path in SHARED_SOUNDS for table in _STOCK_BY_FAMILY.values() for path in table.values()) and _CLACK in SHARED_SOUNDS


def stock_audio_for(reference, engine_types):
    """An existing TPF3 stock sound that stands in for a source audio file nobody can supply.

    The kind of sound is read from the file name (horn, brake, wheel squeal, joint clack, idle; anything else is taken
    for the drive sound) and the family from the vehicle's engines. It is a stand-in, not the original sound."""
    name = reference.replace('\\', '/').rsplit('/', 1)[-1].lower()
    if any(word in name for word in ('horn', 'whistle', 'keiteki')):
        return default_horn(engine_types)
    if 'STEAM' in engine_types:
        family = 'steam'
    elif 'DIESEL' in engine_types:
        family = 'diesel'
    else:
        family = 'electric'
    if any(word in name for word in ('clack', 'joint')):
        return '::/' + _CLACK
    if 'brake' in name or 'bremse' in name:
        kind = 'brake'
    elif any(word in name for word in ('wheel', 'squeal', 'curve', 'ring')):
        kind = 'wheels'
    elif 'idle' in name:
        kind = 'idle'
    else:
        kind = 'drive'
    return '::/' + _STOCK_BY_FAMILY[family][kind]
