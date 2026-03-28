-- GroundWire Supabase schema
-- Run once in the Supabase SQL editor for your project.
-- All statements are idempotent (IF NOT EXISTS / CREATE OR REPLACE).

-- Shared cross-agent quirk store
CREATE TABLE IF NOT EXISTS domain_quirks (
  id               uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
  domain           text        NOT NULL,
  quirk            text        NOT NULL,
  confidence       float       NOT NULL DEFAULT 0.5,
  confirmed_count  int         NOT NULL DEFAULT 0,
  is_global        boolean     NOT NULL DEFAULT false,
  source           text,
  last_seen        timestamptz,
  created_at       timestamptz DEFAULT now(),
  UNIQUE (domain, quirk)
);

CREATE INDEX IF NOT EXISTS idx_domain_quirks_domain_confidence
  ON domain_quirks (domain, confidence DESC);

-- Per-run episodic log
CREATE TABLE IF NOT EXISTS run_episodes (
  id              uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
  domain          text        NOT NULL,
  run_id          text,
  steps           int,
  success         boolean,
  observed_quirks jsonb,
  created_at      timestamptz DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_run_episodes_domain
  ON run_episodes (domain, created_at DESC);

-- Claude-synthesized domain strategic profiles (used by Phase 2)
CREATE TABLE IF NOT EXISTS domain_profiles (
  domain     text        PRIMARY KEY,
  profile    text,
  updated_at timestamptz DEFAULT now()
);

-- Adversarial event history (consumed by Plan B hardener)
CREATE TABLE IF NOT EXISTS antibot_events (
  id                uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
  domain            text        NOT NULL,
  run_id            text,
  block_type        text,
  note              text,
  resolved          boolean,
  resolution_config jsonb,
  created_at        timestamptz DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_antibot_events_domain
  ON antibot_events (domain, created_at DESC);

-- RPC: upsert a quirk and INCREMENT confirmed_count on conflict.
-- Called by shared_memory.promote_if_ready().
-- A plain Python upsert would SET confirmed_count=1 every time;
-- this function does confirmed_count + 1 in SQL so counts accumulate correctly
-- across independent agents, enabling true cross-agent network-effect intelligence.
CREATE OR REPLACE FUNCTION upsert_quirk(
  p_domain    text,
  p_quirk     text,
  p_confidence float,
  p_source    text
) RETURNS void AS $$
BEGIN
  INSERT INTO domain_quirks
    (domain, quirk, confidence, confirmed_count, is_global, last_seen, source)
  VALUES
    (p_domain, p_quirk, p_confidence, 1, true, now(), p_source)
  ON CONFLICT (domain, quirk) DO UPDATE SET
    confirmed_count = domain_quirks.confirmed_count + 1,
    confidence      = GREATEST(domain_quirks.confidence, EXCLUDED.confidence),
    last_seen       = now(),
    source          = EXCLUDED.source;
END;
$$ LANGUAGE plpgsql;
