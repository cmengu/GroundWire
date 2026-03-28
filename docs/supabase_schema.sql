-- Groundwire Supabase Schema
-- Run this in your Supabase SQL editor to enable cross-agent shared memory.
--
-- Setup:
--   1. Create a project at https://supabase.com
--   2. Open the SQL editor and run this file in full
--   3. Add to your .env:
--        SUPABASE_URL=https://<project-ref>.supabase.co
--        SUPABASE_KEY=<anon-public-key>
--   4. Install the optional dep: pip install -e ".[shared]"
--
-- Without these env vars, shared_memory.py degrades to a safe no-op automatically.

-- ── Cross-agent quirk store ────────────────────────────────────────────────────
-- Holds site-specific navigation quirks discovered by any agent on any machine.
-- Quirks are promoted here only once local confidence exceeds the threshold
-- (default: 1.5 — roughly 2+ confirmed sightings on the local machine).

CREATE TABLE IF NOT EXISTS domain_quirks (
    id              bigserial   PRIMARY KEY,
    domain          text        NOT NULL,
    quirk           text        NOT NULL,
    confidence      float       NOT NULL DEFAULT 0.5,
    confirmed_count int         NOT NULL DEFAULT 1,
    source          text,
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now(),
    UNIQUE (domain, quirk)
);

CREATE INDEX IF NOT EXISTS idx_domain_quirks_domain
    ON domain_quirks (domain);

-- RPC called by promote_if_ready() — upserts a quirk and increments confirmed_count.
-- Taking the MAX confidence prevents a low-confidence run from downgrading a proven quirk.
CREATE OR REPLACE FUNCTION upsert_quirk(
    p_domain     text,
    p_quirk      text,
    p_confidence float,
    p_source     text DEFAULT 'groundwire'
) RETURNS void LANGUAGE plpgsql AS $$
BEGIN
    INSERT INTO domain_quirks (domain, quirk, confidence, confirmed_count, source, updated_at)
    VALUES (p_domain, p_quirk, p_confidence, 1, p_source, now())
    ON CONFLICT (domain, quirk) DO UPDATE
        SET confidence      = GREATEST(domain_quirks.confidence, EXCLUDED.confidence),
            confirmed_count = domain_quirks.confirmed_count + 1,
            updated_at      = now();
END;
$$;

-- ── Per-run episodic history ───────────────────────────────────────────────────
-- One row per GroundWire run. Used by the adversarial hardener to check whether
-- a domain has a recent block history before deciding on retry strategy.

CREATE TABLE IF NOT EXISTS run_episodes (
    id              bigserial   PRIMARY KEY,
    domain          text        NOT NULL,
    run_id          uuid,
    steps           int,
    success         boolean     NOT NULL,
    observed_quirks text[]      DEFAULT '{}',
    created_at      timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_run_episodes_domain
    ON run_episodes (domain);

-- ── Anti-bot event log ────────────────────────────────────────────────────────
-- Tracks block detections and their resolutions per domain.
-- Used by AdversarialHardener to build historical context:
-- "this domain switched from Cloudflare to DataDome 3 weeks ago, here's what worked."

CREATE TABLE IF NOT EXISTS antibot_events (
    id                  bigserial   PRIMARY KEY,
    domain              text        NOT NULL,
    run_id              uuid,
    block_type          text,
    note                text,
    resolved            boolean,
    resolution_config   jsonb,
    created_at          timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_antibot_events_domain
    ON antibot_events (domain);
