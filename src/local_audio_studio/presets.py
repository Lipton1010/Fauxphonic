"""Named processing presets shared by the native UI and CLI."""
from .contracts import Preset

_PRESETS = {
    "master-only": ("master-only", False, 6, "program"),
    "natural-speech": ("dsp-cleanup", True, 6, "speech"),
    "strong-cleanup": ("dsp-cleanup", True, 9, "speech"),
    "preserve-music": ("master-only", False, 6, "music"),
    "multitrack": ("dsp-cleanup", True, 6, "speech"),
    "dsp-cleanup": ("dsp-cleanup", False, 6, "speech"),
    "bypass": ("bypass", False, 6, "program"),
}


def make_preset(name, *, target_lufs=None, true_peak=None, highpass_hz=0,
                noise_reduction_db=None, leveler_strength=0.8, preserve_dynamics=0.25,
                max_boost_db=9, max_attenuation_db=12, leveler_enabled=None,
                music_ducking_enabled=False, music_duck_db=6, duck_attack_ms=100,
                duck_release_ms=800, deesser_enabled=False, deesser_range=1.8,
                broad_eq_enabled=False, broad_eq_frequency_hz=1000, broad_eq_gain_db=0,
                bleed_reduction_enabled=False, bleed_max_attenuation_db=6,
                bleed_attack_ms=100, bleed_hold_ms=300, bleed_release_ms=500):
    try:
        mode, leveler, cleanup, _ = _PRESETS[name]
    except KeyError as exc:
        raise ValueError(f"Unknown processing preset: {name}.") from exc
    return Preset(mode=mode, target_lufs=-19 if target_lufs is None else target_lufs,
        true_peak=-1 if true_peak is None else true_peak, highpass_hz=highpass_hz,
        noise_reduction_db=cleanup if noise_reduction_db is None else noise_reduction_db,
        leveler_enabled=leveler if leveler_enabled is None else leveler_enabled,
        leveler_strength=leveler_strength,
        preserve_dynamics=preserve_dynamics, max_boost_db=max_boost_db,
        max_attenuation_db=max_attenuation_db,
        music_ducking_enabled=music_ducking_enabled, music_duck_db=music_duck_db,
        duck_attack_ms=duck_attack_ms, duck_release_ms=duck_release_ms,
        deesser_enabled=deesser_enabled, deesser_range=deesser_range,
        broad_eq_enabled=broad_eq_enabled, broad_eq_frequency_hz=broad_eq_frequency_hz,
        broad_eq_gain_db=broad_eq_gain_db, bleed_reduction_enabled=bleed_reduction_enabled,
        bleed_max_attenuation_db=bleed_max_attenuation_db, bleed_attack_ms=bleed_attack_ms,
        bleed_hold_ms=bleed_hold_ms, bleed_release_ms=bleed_release_ms)


def default_track_role(name):
    try:
        return _PRESETS[name][3]
    except KeyError as exc:
        raise ValueError(f"Unknown processing preset: {name}.") from exc
