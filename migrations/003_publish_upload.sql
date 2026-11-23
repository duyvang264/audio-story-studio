-- Upload results for approved publishing records (YouTube private/unlisted upload, TikTok inbox draft).
ALTER TABLE publishing_records ADD COLUMN external_url TEXT;
ALTER TABLE publishing_records ADD COLUMN last_error TEXT;
ALTER TABLE publishing_records ADD COLUMN published_at TIMESTAMPTZ;
ALTER TABLE publishing_records DROP CONSTRAINT publishing_records_status_check;
ALTER TABLE publishing_records ADD CONSTRAINT publishing_records_status_check CHECK (status IN
    ('awaiting_approval', 'approved', 'ready_manual', 'uploading', 'published', 'uploaded_draft', 'failed', 'cancelled'));
