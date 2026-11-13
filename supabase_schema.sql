-- ==============================================================================
--  SUPABASE DATABASE SCHEMA FOR TEST JIG RESULTS
-- ==============================================================================
--  Instructions:
--  1. Log into your Supabase Dashboard: https://supabase.com/dashboard
--  2. Open your project, go to the "SQL Editor" in the left sidebar.
--  3. Create a "New Query", paste this entire script, and click "Run".
-- ==============================================================================

-- 1. Create the main test results table
CREATE TABLE IF NOT EXISTS public.jig_test_results (
    id BIGSERIAL PRIMARY KEY,
    sr BIGINT NOT NULL,
    jig_id TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    mac TEXT NOT NULL,
    ficr_mac TEXT,
    rssi_dbm DOUBLE PRECISION,
    ble TEXT,
    imu_acctest TEXT,
    mic_result TEXT,
    mic_thd_pct DOUBLE PRECISION,
    mic_snr_db DOUBLE PRECISION,
    led_delta_ma DOUBLE PRECISION,
    active_stream_ma DOUBLE PRECISION,
    active_quiet_ma DOUBLE PRECISION,
    active_3v3 DOUBLE PRECISION,
    active_5v DOUBLE PRECISION,
    active_vbat DOUBLE PRECISION,
    sleep_current_ma DOUBLE PRECISION,
    sleep_3v3 DOUBLE PRECISION,
    sleep_5v DOUBLE PRECISION,
    sleep_vbat DOUBLE PRECISION,
    client_firmware TEXT,
    result TEXT NOT NULL,
    comments TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),

    -- Prevent duplicate insertions if a batch is retransmitted after outage
    CONSTRAINT unique_jig_sr UNIQUE (jig_id, sr)
);

-- 2. Performance Indexes
CREATE INDEX IF NOT EXISTS idx_jig_results_mac ON public.jig_test_results(mac);
CREATE INDEX IF NOT EXISTS idx_jig_results_result ON public.jig_test_results(result);
CREATE INDEX IF NOT EXISTS idx_jig_results_timestamp ON public.jig_test_results(timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_jig_results_jig_id ON public.jig_test_results(jig_id);

-- 3. Row Level Security (RLS) Configuration
-- Enable RLS for table security
ALTER TABLE public.jig_test_results ENABLE ROW LEVEL SECURITY;

-- Allow anon and authenticated keys to insert and read records
CREATE POLICY "Allow public read access"
ON public.jig_test_results
FOR SELECT
TO anon, authenticated
USING (true);

CREATE POLICY "Allow public insert and upsert"
ON public.jig_test_results
FOR INSERT
TO anon, authenticated
WITH CHECK (true);

CREATE POLICY "Allow public update for upserts"
ON public.jig_test_results
FOR UPDATE
TO anon, authenticated
USING (true)
WITH CHECK (true);

