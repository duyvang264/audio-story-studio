-- Audio Story Studio: initial schema

CREATE TABLE stories (
    id            BIGSERIAL PRIMARY KEY,
    slug          TEXT NOT NULL UNIQUE,
    title         TEXT NOT NULL,
    author        TEXT,
    source_path   TEXT,
    rights_type   TEXT NOT NULL DEFAULT 'unknown'
                  CHECK (rights_type IN ('original', 'licensed', 'public_domain', 'unknown')),
    rights_status TEXT NOT NULL DEFAULT 'pending'
                  CHECK (rights_status IN ('pending', 'approved', 'rejected')),
    rights_note   TEXT,
    style_profile TEXT NOT NULL DEFAULT 'dark_fantasy_cinematic',
    metadata      JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE chapters (
    id          BIGSERIAL PRIMARY KEY,
    story_id    BIGINT NOT NULL REFERENCES stories(id) ON DELETE CASCADE,
    chapter_no  INT NOT NULL,
    title       TEXT NOT NULL,
    content     TEXT NOT NULL,
    char_count  INT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (story_id, chapter_no)
);

CREATE TABLE episodes (
    id                     BIGSERIAL PRIMARY KEY,
    story_id               BIGINT NOT NULL REFERENCES stories(id) ON DELETE CASCADE,
    episode_no             INT NOT NULL,
    title                  TEXT NOT NULL,
    chapter_from           INT NOT NULL,
    chapter_to             INT NOT NULL,
    status                 TEXT NOT NULL DEFAULT 'draft',
    current_stage          TEXT,
    narration_duration_sec NUMERIC(10, 3),
    preview_path           TEXT,
    final_path             TEXT,
    last_error             TEXT,
    created_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (story_id, episode_no)
);
CREATE INDEX episodes_status_idx ON episodes (status);

CREATE TABLE scripts (
    id          BIGSERIAL PRIMARY KEY,
    episode_id  BIGINT NOT NULL REFERENCES episodes(id) ON DELETE CASCADE,
    version     INT NOT NULL DEFAULT 1,
    path        TEXT NOT NULL,
    content     JSONB NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (episode_id, version)
);

CREATE TABLE narration_chunks (
    id             BIGSERIAL PRIMARY KEY,
    episode_id     BIGINT NOT NULL REFERENCES episodes(id) ON DELETE CASCADE,
    chunk_no       INT NOT NULL,
    section_no     INT NOT NULL,
    paragraph_no   INT NOT NULL,
    text           TEXT NOT NULL,
    text_hash      TEXT NOT NULL,
    voice          TEXT NOT NULL,
    model_version  TEXT NOT NULL,
    audio_path     TEXT NOT NULL,
    duration_sec   NUMERIC(10, 3) NOT NULL,
    start_sec      NUMERIC(10, 3) NOT NULL,
    end_sec        NUMERIC(10, 3) NOT NULL,
    pause_after_sec NUMERIC(6, 3) NOT NULL DEFAULT 0,
    UNIQUE (episode_id, chunk_no)
);
CREATE INDEX narration_chunks_hash_idx ON narration_chunks (text_hash);

CREATE TABLE visual_assets (
    id             BIGSERIAL PRIMARY KEY,
    kind           TEXT NOT NULL CHECK (kind IN ('character', 'environment', 'scene')),
    path           TEXT NOT NULL UNIQUE,
    sha256         TEXT NOT NULL,
    provider       TEXT NOT NULL,
    prompt         TEXT,
    style_profile  TEXT NOT NULL,
    story_id       BIGINT REFERENCES stories(id) ON DELETE SET NULL,
    character_ids  TEXT[] NOT NULL DEFAULT '{}',
    location_id    TEXT,
    mood           TEXT,
    tags           TEXT[] NOT NULL DEFAULT '{}',
    width          INT,
    height         INT,
    license        TEXT NOT NULL DEFAULT 'generated-internal',
    status         TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'retired')),
    version        INT NOT NULL DEFAULT 1,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX visual_assets_lookup_idx ON visual_assets (style_profile, location_id, status);
CREATE INDEX visual_assets_chars_idx ON visual_assets USING GIN (character_ids);

CREATE TABLE scenes (
    id              BIGSERIAL PRIMARY KEY,
    episode_id      BIGINT NOT NULL REFERENCES episodes(id) ON DELETE CASCADE,
    scene_no        INT NOT NULL,
    start_sec       NUMERIC(10, 3) NOT NULL,
    end_sec         NUMERIC(10, 3) NOT NULL,
    summary         TEXT NOT NULL,
    characters      TEXT[] NOT NULL DEFAULT '{}',
    location_id     TEXT,
    mood            TEXT,
    intensity       NUMERIC(6, 3) NOT NULL DEFAULT 0,
    motion          TEXT NOT NULL,
    prompt          TEXT,
    negative_prompt TEXT,
    asset_id        BIGINT REFERENCES visual_assets(id) ON DELETE SET NULL,
    match_type      TEXT,
    status          TEXT NOT NULL DEFAULT 'planned'
                    CHECK (status IN ('planned', 'asset_ready', 'approved', 'rejected', 'regen_requested')),
    version         INT NOT NULL DEFAULT 1,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (episode_id, scene_no),
    CHECK (end_sec > start_sec)
);

CREATE TABLE asset_usage (
    id          BIGSERIAL PRIMARY KEY,
    asset_id    BIGINT NOT NULL REFERENCES visual_assets(id) ON DELETE CASCADE,
    scene_id    BIGINT NOT NULL REFERENCES scenes(id) ON DELETE CASCADE,
    episode_id  BIGINT NOT NULL REFERENCES episodes(id) ON DELETE CASCADE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (asset_id, scene_id)
);

CREATE TABLE jobs (
    id              BIGSERIAL PRIMARY KEY,
    type            TEXT NOT NULL,
    episode_id      BIGINT REFERENCES episodes(id) ON DELETE CASCADE,
    payload         JSONB NOT NULL DEFAULT '{}'::jsonb,
    idempotency_key TEXT NOT NULL UNIQUE,
    status          TEXT NOT NULL DEFAULT 'queued'
                    CHECK (status IN ('queued', 'running', 'succeeded', 'failed', 'cancelled')),
    priority        INT NOT NULL DEFAULT 100,
    attempts        INT NOT NULL DEFAULT 0,
    max_attempts    INT NOT NULL DEFAULT 3,
    timeout_sec     INT NOT NULL DEFAULT 3600,
    run_after       TIMESTAMPTZ NOT NULL DEFAULT now(),
    locked_by       TEXT,
    locked_at       TIMESTAMPTZ,
    result          JSONB,
    last_error      TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at     TIMESTAMPTZ
);
CREATE INDEX jobs_claim_idx ON jobs (status, priority, run_after, id);
CREATE INDEX jobs_episode_idx ON jobs (episode_id);

CREATE TABLE job_events (
    id          BIGSERIAL PRIMARY KEY,
    job_id      BIGINT REFERENCES jobs(id) ON DELETE CASCADE,
    episode_id  BIGINT REFERENCES episodes(id) ON DELETE CASCADE,
    level       TEXT NOT NULL DEFAULT 'info',
    message     TEXT NOT NULL,
    data        JSONB,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX job_events_episode_idx ON job_events (episode_id, id);

CREATE TABLE approvals (
    id           BIGSERIAL PRIMARY KEY,
    kind         TEXT NOT NULL CHECK (kind IN ('rights', 'content', 'cost', 'publish', 'scene')),
    story_id     BIGINT REFERENCES stories(id) ON DELETE CASCADE,
    episode_id   BIGINT REFERENCES episodes(id) ON DELETE CASCADE,
    scene_id     BIGINT REFERENCES scenes(id) ON DELETE CASCADE,
    status       TEXT NOT NULL DEFAULT 'pending'
                 CHECK (status IN ('pending', 'approved', 'rejected', 'cancelled')),
    payload      JSONB NOT NULL DEFAULT '{}'::jsonb,
    decided_by   BIGINT,
    note         TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    decided_at   TIMESTAMPTZ
);
CREATE UNIQUE INDEX approvals_one_pending_idx ON approvals
    (kind, COALESCE(story_id, 0), COALESCE(episode_id, 0), COALESCE(scene_id, 0))
    WHERE status = 'pending';

CREATE TABLE provider_costs (
    id             BIGSERIAL PRIMARY KEY,
    period         TEXT NOT NULL,                 -- YYYY-MM
    category       TEXT NOT NULL CHECK (category IN ('image', 'tts', 'other')),
    provider       TEXT NOT NULL,
    is_paid        BOOLEAN NOT NULL,
    status         TEXT NOT NULL CHECK (status IN ('reserved', 'committed', 'released')),
    estimated_vnd  NUMERIC(12, 0) NOT NULL DEFAULT 0,
    actual_vnd     NUMERIC(12, 0),
    units          NUMERIC(12, 3) NOT NULL DEFAULT 1,
    job_id         BIGINT REFERENCES jobs(id) ON DELETE SET NULL,
    episode_id     BIGINT REFERENCES episodes(id) ON DELETE SET NULL,
    note           TEXT,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    settled_at     TIMESTAMPTZ
);
CREATE INDEX provider_costs_period_idx ON provider_costs (period, category, status);

CREATE TABLE publishing_records (
    id           BIGSERIAL PRIMARY KEY,
    episode_id   BIGINT NOT NULL REFERENCES episodes(id) ON DELETE CASCADE,
    platform     TEXT NOT NULL CHECK (platform IN ('youtube', 'tiktok')),
    video_path   TEXT NOT NULL,
    title        TEXT NOT NULL,
    description  TEXT,
    tags         TEXT[] NOT NULL DEFAULT '{}',
    status       TEXT NOT NULL DEFAULT 'awaiting_approval'
                 CHECK (status IN ('awaiting_approval', 'approved', 'ready_manual', 'published', 'failed', 'cancelled')),
    approval_id  BIGINT REFERENCES approvals(id) ON DELETE SET NULL,
    external_id  TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (episode_id, platform, video_path)
);

CREATE TABLE telegram_state (
    user_id     BIGINT PRIMARY KEY,
    pending     TEXT,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE telegram_updates (
    update_id   BIGINT PRIMARY KEY,
    received_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
