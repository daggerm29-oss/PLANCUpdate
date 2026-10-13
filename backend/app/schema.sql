-- Canonical schema. SQLite now; written to port to PostgreSQL (see ARCHITECTURE.md §7).

CREATE TABLE IF NOT EXISTS sources (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  homepage TEXT,
  licence TEXT,
  notes TEXT
);

CREATE TABLE IF NOT EXISTS ingest_runs (
  id INTEGER PRIMARY KEY,
  source_id TEXT NOT NULL REFERENCES sources(id),
  started_at TEXT NOT NULL,
  finished_at TEXT,
  status TEXT NOT NULL DEFAULT 'running',          -- running | success | partial | failed | skipped
  params TEXT,
  stats TEXT,
  error TEXT
);

-- Original payloads, never modified in place without archiving.
CREATE TABLE IF NOT EXISTS raw_records (
  id INTEGER PRIMARY KEY,
  source_id TEXT NOT NULL REFERENCES sources(id),
  record_key TEXT NOT NULL,
  url TEXT,
  content_type TEXT NOT NULL DEFAULT 'application/json',
  payload TEXT NOT NULL,
  content_hash TEXT NOT NULL,
  first_seen_at TEXT NOT NULL,
  last_fetched_at TEXT NOT NULL,
  last_changed_at TEXT NOT NULL,
  ingest_run_id INTEGER REFERENCES ingest_runs(id),
  UNIQUE (source_id, record_key)
);

-- Previous versions of changed raw records (corrections / historical analysis).
CREATE TABLE IF NOT EXISTS raw_record_history (
  id INTEGER PRIMARY KEY,
  raw_record_id INTEGER NOT NULL REFERENCES raw_records(id) ON DELETE CASCADE,
  payload TEXT NOT NULL,
  content_hash TEXT NOT NULL,
  valid_from TEXT NOT NULL,
  valid_to TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_raw_history_record ON raw_record_history(raw_record_id);

CREATE TABLE IF NOT EXISTS entities (
  id TEXT PRIMARY KEY,                               -- gleif:<LEI>, os:<id>
  source_id TEXT NOT NULL REFERENCES sources(id),
  kind TEXT NOT NULL,                                -- company | organization | person | asset
  name TEXT NOT NULL,
  name_norm TEXT NOT NULL,
  country TEXT,
  jurisdiction TEXT,
  status TEXT,
  legal_form TEXT,
  incorporation_date TEXT,
  dissolution_date TEXT,
  address TEXT,
  address_norm TEXT,
  topics TEXT NOT NULL DEFAULT '',                   -- comma separated, e.g. sanction,role.pep
  attrs TEXT NOT NULL DEFAULT '{}',                  -- JSON
  raw_record_id INTEGER REFERENCES raw_records(id),
  first_seen_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_entities_source ON entities(source_id, kind);
CREATE INDEX IF NOT EXISTS idx_entities_name_norm ON entities(name_norm);
CREATE INDEX IF NOT EXISTS idx_entities_address_norm ON entities(address_norm);

CREATE TABLE IF NOT EXISTS entity_names (
  entity_id TEXT NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
  name TEXT NOT NULL,
  name_norm TEXT NOT NULL,
  name_type TEXT NOT NULL DEFAULT 'alias',           -- legal | alias | previous | transliteration
  PRIMARY KEY (entity_id, name_norm, name_type)
);
CREATE INDEX IF NOT EXISTS idx_entity_names_norm ON entity_names(name_norm);

CREATE TABLE IF NOT EXISTS identifiers (
  entity_id TEXT NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
  scheme TEXT NOT NULL,                              -- lei | bic | ogrn | inn | imo | registration_number ...
  value TEXT NOT NULL,
  PRIMARY KEY (entity_id, scheme, value)
);
CREATE INDEX IF NOT EXISTS idx_identifiers_lookup ON identifiers(scheme, value);
CREATE INDEX IF NOT EXISTS idx_identifiers_value ON identifiers(value);

CREATE TABLE IF NOT EXISTS relationships (
  id INTEGER PRIMARY KEY,
  source_id TEXT NOT NULL REFERENCES sources(id),
  subject_id TEXT NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
  predicate TEXT NOT NULL,
  object_id TEXT NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
  basis TEXT NOT NULL DEFAULT 'documented',          -- documented | reported | inferred
  confidence REAL NOT NULL DEFAULT 1.0,
  start_date TEXT,
  end_date TEXT,
  status TEXT,
  attrs TEXT NOT NULL DEFAULT '{}',
  raw_record_id INTEGER REFERENCES raw_records(id),
  first_seen_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE (source_id, subject_id, predicate, object_id)
);
CREATE INDEX IF NOT EXISTS idx_rel_subject ON relationships(subject_id, predicate);
CREATE INDEX IF NOT EXISTS idx_rel_object ON relationships(object_id, predicate);

CREATE TABLE IF NOT EXISTS evidence (
  id INTEGER PRIMARY KEY,
  key TEXT NOT NULL UNIQUE,
  entity_id TEXT REFERENCES entities(id) ON DELETE CASCADE,
  relationship_id INTEGER REFERENCES relationships(id) ON DELETE CASCADE,
  source_id TEXT NOT NULL REFERENCES sources(id),
  url TEXT,
  title TEXT,
  excerpt TEXT,
  raw_record_id INTEGER REFERENCES raw_records(id),
  retrieved_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_evidence_entity ON evidence(entity_id);
CREATE INDEX IF NOT EXISTS idx_evidence_rel ON evidence(relationship_id);

CREATE TABLE IF NOT EXISTS match_candidates (
  id INTEGER PRIMARY KEY,
  left_id TEXT NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
  right_id TEXT NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
  method TEXT NOT NULL,                              -- identifier | name_fuzzy
  score REAL NOT NULL,
  features TEXT NOT NULL DEFAULT '{}',
  status TEXT NOT NULL DEFAULT 'pending',            -- pending | auto | confirmed | rejected
  reviewer_note TEXT,
  created_at TEXT NOT NULL,
  reviewed_at TEXT,
  UNIQUE (left_id, right_id)
);
CREATE INDEX IF NOT EXISTS idx_match_status ON match_candidates(status);
CREATE INDEX IF NOT EXISTS idx_match_right ON match_candidates(right_id);

-- Full-text index; rowid = entities.rowid
CREATE VIRTUAL TABLE IF NOT EXISTS entity_search USING fts5(
  entity_id UNINDEXED,
  names,
  identifiers,
  tokenize = 'unicode61 remove_diacritics 2'
);

-- AI request ledger: shared by every process, used for RPM / concurrency / daily budget.
CREATE TABLE IF NOT EXISTS ai_request_log (
  id INTEGER PRIMARY KEY,
  started_at REAL NOT NULL,
  finished_at REAL,
  lease_expires_at REAL,
  status TEXT NOT NULL,                              -- in_flight | ok | error | rate_limited | timeout | abandoned
  task TEXT,
  model TEXT,
  priority INTEGER,
  attempt INTEGER NOT NULL DEFAULT 0,
  cache_key TEXT,
  prompt_chars INTEGER,
  http_status INTEGER,
  error_kind TEXT,
  input_tokens INTEGER,
  output_tokens INTEGER,
  total_tokens INTEGER,
  latency_ms INTEGER,
  process_id TEXT
);
CREATE INDEX IF NOT EXISTS idx_ai_log_started ON ai_request_log(started_at);
CREATE INDEX IF NOT EXISTS idx_ai_log_status ON ai_request_log(status);

CREATE TABLE IF NOT EXISTS ai_cache (
  key TEXT PRIMARY KEY,
  task TEXT,
  model TEXT,
  fingerprint TEXT,
  response TEXT NOT NULL,
  usage TEXT,
  created_at REAL NOT NULL,
  expires_at REAL NOT NULL,
  hits INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS ai_state (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ai_counters (
  name TEXT PRIMARY KEY,
  value INTEGER NOT NULL DEFAULT 0
);

-- Material statements from unstructured sources (crawler). Kept apart from facts until verified.
CREATE TABLE IF NOT EXISTS claims (
  id INTEGER PRIMARY KEY,
  key TEXT NOT NULL UNIQUE,
  subject_entity_id TEXT REFERENCES entities(id) ON DELETE SET NULL,
  subject_query TEXT NOT NULL,
  claim_type TEXT NOT NULL,                          -- identifier | attribute | director | associated_company | acquisition | overview
  field TEXT NOT NULL,
  value TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'unverified',         -- unverified | verified | conflict | related
  source_id TEXT NOT NULL REFERENCES sources(id),
  source_url TEXT,
  source_domain TEXT,
  retrieved_at TEXT NOT NULL,
  evidence_excerpt TEXT,
  extraction_method TEXT NOT NULL,
  confidence REAL NOT NULL,
  raw_record_id INTEGER REFERENCES raw_records(id),
  verification TEXT NOT NULL DEFAULT '{}',           -- JSON: how the status was decided
  first_seen_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_claims_subject ON claims(subject_entity_id);
CREATE INDEX IF NOT EXISTS idx_claims_query ON claims(subject_query);
CREATE INDEX IF NOT EXISTS idx_claims_status ON claims(status);

-- Crawler response cache + cross-process in-flight lease (one upstream call per query at a time).
CREATE TABLE IF NOT EXISTS crawl_requests (
  key TEXT PRIMARY KEY,
  query TEXT NOT NULL,
  status TEXT NOT NULL,                              -- in_flight | ok | not_found | error
  http_status INTEGER,
  response TEXT,
  error TEXT,
  raw_record_id INTEGER REFERENCES raw_records(id),
  created_at REAL NOT NULL,
  updated_at REAL NOT NULL,
  expires_at REAL,
  lease_expires_at REAL,
  hits INTEGER NOT NULL DEFAULT 0
);

-- Every upstream provider attempt (and cache hits / local rejections) for limits and monitoring.
CREATE TABLE IF NOT EXISTS provider_usage (
  id INTEGER PRIMARY KEY,
  provider TEXT NOT NULL,                            -- crawler
  operation TEXT NOT NULL,                           -- scrape | health
  request_key TEXT,
  started_at REAL NOT NULL,
  finished_at REAL,
  status TEXT NOT NULL,                              -- in_flight | ok | not_found | error | timeout | cache_hit | deduplicated | rejected
  http_status INTEGER,
  attempt INTEGER NOT NULL DEFAULT 0,
  latency_ms INTEGER,
  credits_estimate INTEGER NOT NULL DEFAULT 0,       -- upper-bound Serper credits for this attempt
  error TEXT
);
CREATE INDEX IF NOT EXISTS idx_provider_usage_started ON provider_usage(provider, started_at);

-- Saved investigations (API/UI planned in P10)
CREATE TABLE IF NOT EXISTS investigations (
  id INTEGER PRIMARY KEY,
  title TEXT NOT NULL,
  notes TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS investigation_items (
  id INTEGER PRIMARY KEY,
  investigation_id INTEGER NOT NULL REFERENCES investigations(id) ON DELETE CASCADE,
  item_type TEXT NOT NULL,                           -- entity | relationship | path | note
  ref TEXT NOT NULL,
  note TEXT,
  created_at TEXT NOT NULL
);
