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
