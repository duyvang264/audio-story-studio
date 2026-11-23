import os
import tempfile

import pytest

_TMP = tempfile.mkdtemp(prefix="ass-test-")
os.environ.update({
    "DATABASE_URL": os.environ.get("TEST_DATABASE_URL", "postgresql:///audio_story_studio_test"),
    "STORAGE_DIR": os.path.join(_TMP, "storage"),
    "API_KEY": "test-key",
    "TELEGRAM_BOT_TOKEN": "",
    "TELEGRAM_DRY_RUN": "true",
    "TELEGRAM_ALLOWED_USER_IDS": "111",
    "TELEGRAM_NOTIFY_CHAT_ID": "111",
    "TTS_PROVIDER": "tone",
    "IMAGE_PROVIDER": "mock",
    "TARGET_SCENE_DURATION_SEC": "6",
    "MIN_SCENE_DURATION_SEC": "3",
    "MAX_SCENE_DURATION_SEC": "9",
    "PREVIEW_HEIGHT": "180",
    "FINAL_HEIGHT": "360",
    "TRANSITION_SEC": "0.4",
    "TIKTOK_HEIGHT": "320",
    "TIKTOK_MIN_SEC": "4",
    "TIKTOK_MAX_SEC": "9",
    "TIKTOK_TARGET_SEC": "6",
    "N8N_EVENT_WEBHOOK_URL": "",
    "BGM_PATH": "",
    "AUTO_CHAIN": "true",
})

from studio.config import ensure_storage, get_settings  # noqa: E402

get_settings.cache_clear()


@pytest.fixture(scope="session")
def settings():
    s = get_settings()
    ensure_storage(s)
    return s


@pytest.fixture(scope="session")
def db(settings):
    import psycopg
    from studio.db import migrate
    url = settings.database_url
    assert url.rstrip("/").endswith("_test"), "refusing to reset a non-test database"
    with psycopg.connect(url, autocommit=True) as c:
        c.execute("DROP SCHEMA public CASCADE")
        c.execute("CREATE SCHEMA public")
    migrate()
    return url
