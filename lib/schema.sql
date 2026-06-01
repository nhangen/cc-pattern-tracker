-- Pattern-tracker holds only the DELTA features not already in ~/.claude/__store.db.
-- At report time we join on session_id + timestamp to pull model, cost_usd, duration_ms.
-- Never duplicate __store.db columns here.
CREATE TABLE IF NOT EXISTS events (
  event_id           TEXT PRIMARY KEY,
  session_id         TEXT NOT NULL,
  timestamp          INTEGER NOT NULL,
  tool_name          TEXT NOT NULL,
  cwd                TEXT,
  branch             TEXT,
  prior_tools_5min   TEXT,
  prior_errors_count INTEGER DEFAULT 0,
  prompt_hash        TEXT,
  rules_loaded_hash  TEXT,
  exit_code          INTEGER,
  error_tail         TEXT,
  pre_complete       INTEGER DEFAULT 0,
  post_complete      INTEGER DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_events_session_id ON events(session_id);
CREATE INDEX IF NOT EXISTS idx_events_timestamp  ON events(timestamp);
CREATE INDEX IF NOT EXISTS idx_events_tool_name  ON events(tool_name);

CREATE TABLE IF NOT EXISTS findings (
  finding_id     TEXT PRIMARY KEY,
  pr_url         TEXT NOT NULL,
  session_id     TEXT,
  ts             INTEGER NOT NULL,
  severity       TEXT NOT NULL,
  category       TEXT,
  file_path      TEXT,
  line_no        INTEGER,
  summary        TEXT NOT NULL,
  evidence       TEXT,
  panel_variant  TEXT,
  source         TEXT NOT NULL DEFAULT 'pr-review-panel'
);

CREATE INDEX IF NOT EXISTS idx_findings_pr_url     ON findings(pr_url);
CREATE INDEX IF NOT EXISTS idx_findings_session_id ON findings(session_id);
CREATE INDEX IF NOT EXISTS idx_findings_severity   ON findings(severity);
