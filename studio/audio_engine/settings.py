"""Editorial targets and calibrated heuristics, configurable with AUDIO__* env vars."""
from pydantic import BaseModel, Field, model_validator


class AudioSettings(BaseModel):
    sample_rate: int = Field(48000, ge=16000, le=96000)
    channels: int = Field(2, ge=1, le=2)
    aac_bitrate: str = "256k"
    target_lufs: float = Field(-16, ge=-30, le=-10)
    loudness_tolerance_lu: float = Field(1, gt=0, le=4)
    max_true_peak_dbtp: float = Field(-1.5, le=-1, ge=-6)
    codec_headroom_db: float = Field(0.75, ge=0, le=3)
    target_lra_lu: float = Field(7, ge=1, le=20)
    sync_tolerance_ms: float = Field(100, ge=1, le=500)
    silence_threshold_db: float = -55
    max_unexpected_silence_sec: float = Field(3, gt=0)
    clip_threshold: float = Field(0.999, gt=0.95, le=1)
    critical_flat_samples: int = Field(3, ge=2)
    click_min_jump: float = Field(0.18, gt=0)
    click_adaptive_ratio: float = Field(10, ge=4)
    noise_floor_warning_db: float = -48
    hum_ratio_warning: float = Field(0.35, gt=0, lt=1)
    rumble_ratio_warning: float = Field(0.5, gt=0, lt=1)
    max_auto_repair_attempts: int = Field(2, ge=0, le=2)
    auto_repair_clicks: bool = True
    auto_remove_dc: bool = True
    conditional_highpass: bool = True
    conditional_denoise: bool = False
    conditional_deess: bool = False
    conditional_compression: bool = False
    require_listening_review: bool = True
    duck_min_db: float = Field(8, ge=0, le=24)
    duck_max_db: float = Field(14, ge=0, le=24)
    duck_attack_ms: float = Field(80, gt=0)
    duck_release_ms: float = Field(600, gt=0)
    sfx_duck_db: float = Field(8, ge=0, le=24)
    masking_margin_db: float = Field(6, ge=0)
    max_masked_fraction: float = Field(0.1, ge=0, le=1)
    max_masking_run_ms: float = Field(200, gt=0)
    sound_design_enabled: bool = True
    procedural_fallback: bool = True
    asset_catalog: str = "config/sound_assets.json"
    export_review_clips: bool = True
    max_review_clips: int = Field(16, ge=0, le=100)
    accelerated_visuals: bool = True
    fast_visual_sec: float = Field(35, ge=5, le=120)

    @model_validator(mode='after')
    def gain_order(self):
        if self.duck_max_db < self.duck_min_db:
            raise ValueError('duck_max_db must be >= duck_min_db')
        return self
