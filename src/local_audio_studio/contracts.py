"""Small versioned project contract. All offsets are integer 48 kHz samples."""
from dataclasses import asdict, dataclass, field
from pathlib import Path
import json
import math

RATE = 48000


@dataclass
class GainPoint:
    """Source-local automation point in integer 48 kHz samples."""
    sample: int
    gain_db: float


@dataclass
class Track:
    path: str
    offset_samples: int = 0
    gain_db: float = 0.0
    role: str = "program"
    stream: int | None = None
    gain_envelope: list[GainPoint] = field(default_factory=list)
    bleed_protected: bool = False
    leveler_curve_imported: bool = False

    def validate(self):
        if not Path(self.path).is_file():
            raise ValueError(f"Source not found: {self.path}")
        if type(self.offset_samples) is not int or self.offset_samples < 0:
            raise ValueError("Track offsets must be nonnegative integer project samples.")
        if not math.isfinite(self.gain_db) or not -60 <= self.gain_db <= 24:
            raise ValueError("Track gain must be between -60 and +24 dB.")
        if self.role not in {"program", "speech", "music", "ambience"}:
            raise ValueError("Unknown track role.")
        if type(self.bleed_protected) is not bool:
            raise ValueError("Bleed protection must be enabled or disabled.")
        if type(self.leveler_curve_imported) is not bool:
            raise ValueError("Imported leveler curve provenance must be enabled or disabled.")
        if self.stream is not None and (type(self.stream) is not int or self.stream < 0):
            raise ValueError("Audio stream index must be a nonnegative integer.")
        previous = -1
        for point in self.gain_envelope:
            if type(point.sample) is not int or point.sample < 0 or point.sample <= previous:
                raise ValueError("Gain envelope samples must be strictly increasing nonnegative integers.")
            if not math.isfinite(point.gain_db) or not -60 <= point.gain_db <= 24:
                raise ValueError("Gain envelope points must be between -60 and +24 dB.")
            previous = point.sample


@dataclass
class Preset:
    mode: str = "master-only"
    target_lufs: float = -19.0
    true_peak: float = -1.0
    highpass_hz: int = 0
    noise_reduction_db: int = 6
    leveler_enabled: bool = False
    leveler_strength: float = 0.8
    preserve_dynamics: float = 0.25
    max_boost_db: float = 9.0
    max_attenuation_db: float = 12.0
    music_ducking_enabled: bool = False
    music_duck_db: float = 6.0
    duck_attack_ms: int = 100
    duck_release_ms: int = 800
    bleed_reduction_enabled: bool = False
    bleed_max_attenuation_db: float = 6.0
    bleed_attack_ms: int = 100
    bleed_hold_ms: int = 300
    bleed_release_ms: int = 500
    deesser_enabled: bool = False
    deesser_range: float = 1.8
    broad_eq_enabled: bool = False
    broad_eq_frequency_hz: int = 1000
    broad_eq_gain_db: float = 0.0

    def validate(self):
        if any(type(value) is not bool for value in (self.leveler_enabled, self.music_ducking_enabled,
                self.deesser_enabled, self.broad_eq_enabled, self.bleed_reduction_enabled)):
            raise ValueError("Processing switches must be enabled or disabled.")
        if self.mode not in {"master-only", "dsp-cleanup", "bypass"}:
            raise ValueError("Unavailable processing mode.")
        if not -36 <= self.target_lufs <= -5 or not -9 <= self.true_peak <= -0.3:
            raise ValueError("Loudness target must be -36..-5 LUFS; peak -9..-0.3 dBTP.")
        if not 0 <= self.highpass_hz <= 150 or not 0 <= self.noise_reduction_db <= 12:
            raise ValueError("Invalid conservative DSP settings.")
        if (not math.isfinite(self.leveler_strength) or not 0 <= self.leveler_strength <= 1
                or not math.isfinite(self.preserve_dynamics) or not 0 <= self.preserve_dynamics <= 1):
            raise ValueError("Leveler strength and dynamics preservation must be between 0 and 1.")
        if (not math.isfinite(self.max_boost_db) or not 0 <= self.max_boost_db <= 18
                or not math.isfinite(self.max_attenuation_db) or not 0 <= self.max_attenuation_db <= 24):
            raise ValueError("Leveler boost must be 0..18 dB and attenuation 0..24 dB.")
        if (not math.isfinite(self.music_duck_db) or not 0 <= self.music_duck_db <= 12
                or type(self.duck_attack_ms) is not int or not 50 <= self.duck_attack_ms <= 150
                or type(self.duck_release_ms) is not int or not 500 <= self.duck_release_ms <= 1500):
            raise ValueError("Music ducking must be 0..12 dB, attack 50..150 ms, release 500..1500 ms.")
        if (not math.isfinite(self.bleed_max_attenuation_db) or not 0 <= self.bleed_max_attenuation_db <= 6
                or type(self.bleed_attack_ms) is not int or not 50 <= self.bleed_attack_ms <= 500
                or type(self.bleed_hold_ms) is not int or not 100 <= self.bleed_hold_ms <= 1000
                or type(self.bleed_release_ms) is not int or not 100 <= self.bleed_release_ms <= 2000):
            raise ValueError("Bleed attenuation must be 0..6 dB; attack 50..500 ms, hold 100..1000 ms, release 100..2000 ms.")
        if type(self.deesser_enabled) is not bool or not math.isfinite(self.deesser_range) or not 0 <= self.deesser_range <= 1.8:
            raise ValueError("De-essing must be disabled or use a filter range setting between 0 and 1.8.")
        if (type(self.broad_eq_enabled) is not bool or type(self.broad_eq_frequency_hz) is not int
                or not 200 <= self.broad_eq_frequency_hz <= 8000
                or not math.isfinite(self.broad_eq_gain_db) or not -3 <= self.broad_eq_gain_db <= 3):
            raise ValueError("Broad EQ must use a 200..8000 Hz center and gain between -3 and +3 dB.")


@dataclass
class Project:
    tracks: list[Track] = field(default_factory=list)
    preset: Preset = field(default_factory=Preset)
    version: int = 1
    sample_rate: int = RATE
    export_stems: bool = False
    metadata: dict[str, str] = field(default_factory=dict)

    def validate(self):
        if self.version != 1 or self.sample_rate != RATE:
            raise ValueError("Unsupported project schema or sample rate.")
        if not self.tracks or len(self.tracks) > 32:
            raise ValueError("Import between 1 and 32 tracks.")
        self.preset.validate()
        if type(self.export_stems) is not bool:
            raise ValueError("Stem export must be enabled or disabled.")
        allowed_tags = {"title", "artist", "album", "date", "genre", "comment", "copyright"}
        if (not isinstance(self.metadata, dict) or set(self.metadata) - allowed_tags
                or any(not isinstance(value, str) or len(value) > 4096 or "\0" in value
                       for value in self.metadata.values())):
            raise ValueError("Metadata must use supported text tags, each at most 4096 characters without NUL.")
        for track in self.tracks:
            track.validate()

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, data):
        data = dict(data)
        data["tracks"] = [Track(**{**t, "gain_envelope": [
            point if isinstance(point, GainPoint) else GainPoint(**point)
            for point in t.get("gain_envelope", [])]}) for t in data["tracks"]]
        data["preset"] = Preset(**data["preset"])
        project = cls(**data)
        project.validate()
        return project

    @classmethod
    def load(cls, path):
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        for track in data["tracks"]:
            source = Path(track["path"])
            track["path"] = str((Path(path).parent / source).resolve())
        return cls.from_dict(data)
