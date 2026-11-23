-- Distinguish automatic image generation from user-requested scene regeneration,
-- which have separate per-episode limits.
ALTER TABLE provider_costs ADD COLUMN purpose TEXT NOT NULL DEFAULT 'generate'
    CHECK (purpose IN ('generate', 'regen'));
