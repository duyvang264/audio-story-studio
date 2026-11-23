-- Images the user rejected for a scene are never auto-matched to that scene again.
ALTER TABLE scenes ADD COLUMN rejected_asset_ids BIGINT[] NOT NULL DEFAULT '{}';
