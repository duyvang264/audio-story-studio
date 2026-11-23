"""Runtime configuration loaded from environment / .env."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from .audio_engine.settings import AudioSettings
from .horror_story import StorySettings

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env", env_file_encoding="utf-8", extra="ignore", env_nested_delimiter="__"
    )

    database_url: str = "postgresql:///audio_story_studio"
    audio: AudioSettings = Field(default_factory=AudioSettings)
    story_engine: StorySettings = Field(default_factory=StorySettings)
    storage_dir: Path = Path("storage")
    api_key: str = ""
    api_host: str = "127.0.0.1"
    api_port: int = 8765

    telegram_bot_token: str = ""
    telegram_allowed_user_ids: str = ""
    telegram_mode: Literal["n8n_webhook", "polling", "disabled"] = "polling"
    telegram_dry_run: bool = False
    telegram_notify_chat_id: str = ""

    tts_provider: Literal["macos_say", "piper", "tone", "vieneu"] = "macos_say"
    tts_voice: str = "Linh"
    tts_rate: int = 170
    piper_model: str = "storage/models/piper/vi_VN-vais1000-medium.onnx"
    piper_speed: int = 100
    vieneu_python: str = "storage/tools/vieneu/venv/bin/python"
    vieneu_voice: str = "Thiện Minh"
    tts_pronunciations: dict[str, str] = Field(default_factory=dict)

    # burn = drawn into the picture (Pillow overlay), soft = mov_text track, both = burn + soft
    subtitle_mode: Literal["burn", "soft", "both"] = "both"

    image_provider: Literal["mock", "codex", "local"] = "mock"
    codex_imagegen_verified: bool = False
    codex_timeout_sec: int = 600
    codex_max_images_per_day: int = 8
    local_image_endpoint: str = ""

    monthly_budget_vnd: int = Field(500_000, ge=0, le=500_000)
    image_budget_vnd: int = 200_000
    tts_budget_vnd: int = 200_000
    reserve_budget_vnd: int = 100_000
    max_new_images_per_episode: int = 4
    max_regen_images_per_episode: int = 2
    target_scene_duration_sec: float = 120
    min_scene_duration_sec: float = 45
    max_scene_duration_sec: float = 150
    allow_paid_api: bool = False
    require_approval_for_paid_generation: bool = True
    allow_auto_publish: bool = False

    auto_chain: bool = True
    n8n_event_webhook_url: str = ""
    bgm_path: str = "storage/assets/bgm/dark_ambient.m4a"
    bgm_volume: float = 0.10

    # Uploads run only after a human approves the `publish` approval, and only when enabled.
    publish_upload_enabled: bool = False
    google_drive_client_secrets: str = "storage/secrets/google_drive_client_secret.json"
    youtube_client_secrets: str = "storage/secrets/youtube_client_secret.json"
    youtube_privacy: Literal["private", "unlisted", "public"] = "private"
    youtube_category_id: str = "24"
    tiktok_client_key: str = ""
    tiktok_client_secret: str = ""
    tiktok_redirect_uri: str = ""

    # TikTok / Shorts 9:16 cut of the climax, rendered after the final YouTube video passes QC
    tiktok_enabled: bool = True
    tiktok_height: int = 1920
    tiktok_min_sec: float = 60
    tiktok_max_sec: float = 120
    tiktok_target_sec: float = 90
    tiktok_lufs: float = -14

    fps: int = 25
    transition_sec: float = 1.0
    preview_height: int = 720
    final_height: int = 1080
    telegram_max_upload_mb: int = 48

    worker_id: str = Field(default_factory=lambda: f"worker-{__import__('os').getpid()}")

    @property
    def allowed_user_ids(self) -> set[int]:
        out = set()
        for part in self.telegram_allowed_user_ids.split(","):
            part = part.strip()
            if part.lstrip("-").isdigit():
                out.add(int(part))
        return out

    @property
    def storage(self) -> Path:
        p = self.storage_dir
        return p if p.is_absolute() else PROJECT_ROOT / p

    def project_path(self, value: str) -> Path | None:
        """Resolve a configured path relative to the project root; None when empty."""
        if not value:
            return None
        p = Path(value)
        return p if p.is_absolute() else PROJECT_ROOT / p

    def tts_args(self) -> tuple[str, str, int]:
        """(provider, voice, rate) for the configured TTS provider."""
        if self.tts_provider == "piper":
            return "piper", str(self.project_path(self.piper_model)), self.piper_speed
        if self.tts_provider == "vieneu":
            return "vieneu", self.vieneu_voice, 100
        return self.tts_provider, self.tts_voice, self.tts_rate


@lru_cache
def get_settings() -> Settings:
    return Settings()


STORAGE_SUBDIRS = [
    "stories",
    "episodes",
    "audio/cache",
    "audio/episodes",
    "assets/characters",
    "assets/environments",
    "assets/scenes",
    "assets/bgm",
    "output",
    "previews",
    "logs",
]


def ensure_storage(settings: Settings | None = None) -> Path:
    s = settings or get_settings()
    for sub in STORAGE_SUBDIRS:
        (s.storage / sub).mkdir(parents=True, exist_ok=True)
    return s.storage
