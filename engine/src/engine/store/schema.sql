-- Lorebound engine schema (SQLite). Idempotent: safe to run on every connect.
-- JSON columns store json.dumps(...) text; see models.JSON_FIELDS for the map.
-- dict keys everywhere == column names (models.to_row / from_row handle coding).

CREATE TABLE IF NOT EXISTS world (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  seed TEXT NOT NULL DEFAULT '',
  day INTEGER NOT NULL DEFAULT 1,
  hour INTEGER NOT NULL DEFAULT 8,
  minute INTEGER NOT NULL DEFAULT 0,
  active_scene_id TEXT NOT NULL DEFAULT '',
  created_at INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS locations (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL DEFAULT '',
  description_static TEXT NOT NULL DEFAULT '',
  connections TEXT NOT NULL DEFAULT '[]',
  flags TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS characters (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL DEFAULT '',
  stats TEXT NOT NULL DEFAULT '{}',
  inventory TEXT NOT NULL DEFAULT '[]',
  location_id TEXT NOT NULL DEFAULT '',
  status_effects TEXT NOT NULL DEFAULT '[]',
  known_facts TEXT NOT NULL DEFAULT '[]',
  journal TEXT NOT NULL DEFAULT '[]'
);

CREATE TABLE IF NOT EXISTS npcs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL DEFAULT '',
  personality TEXT NOT NULL DEFAULT '{}',
  disposition_base REAL NOT NULL DEFAULT 0.0,
  location_id TEXT NOT NULL DEFAULT '',
  alive INTEGER NOT NULL DEFAULT 1,
  schedule TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS leads (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  title TEXT NOT NULL DEFAULT '',
  stage TEXT NOT NULL DEFAULT 'unheard',
  stage_history TEXT NOT NULL DEFAULT '[]',
  known_by TEXT NOT NULL DEFAULT '[]',
  related_npc_ids TEXT NOT NULL DEFAULT '[]',
  related_fact_ids TEXT NOT NULL DEFAULT '[]'
);

CREATE TABLE IF NOT EXISTS world_facts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  statement TEXT NOT NULL DEFAULT '',
  pinned INTEGER NOT NULL DEFAULT 0,
  established_turn INTEGER NOT NULL DEFAULT 0,
  source TEXT NOT NULL DEFAULT '',
  tags TEXT NOT NULL DEFAULT '[]',
  contradicts TEXT NOT NULL DEFAULT '[]'
);

CREATE TABLE IF NOT EXISTS turn_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  turn INTEGER NOT NULL DEFAULT 0,
  actor TEXT NOT NULL DEFAULT '',
  raw_action TEXT NOT NULL DEFAULT '',
  mechanical_resolution TEXT NOT NULL DEFAULT '{}',
  narration_text TEXT NOT NULL DEFAULT '',
  state_deltas_applied TEXT NOT NULL DEFAULT '[]',
  created_at INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS npc_memory (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  npc_id TEXT NOT NULL DEFAULT '',
  turn_established INTEGER NOT NULL DEFAULT 0,
  statement TEXT NOT NULL DEFAULT '',
  type TEXT NOT NULL DEFAULT 'observed',
  sentiment REAL NOT NULL DEFAULT 0.0,
  decay_rate REAL NOT NULL DEFAULT 0.0,
  reinforced_count INTEGER NOT NULL DEFAULT 0,
  last_referenced_turn INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS chronicle (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  turn_id INTEGER NOT NULL DEFAULT 0,
  actor TEXT NOT NULL DEFAULT '',
  action_summary TEXT NOT NULL DEFAULT '',
  mechanical_result TEXT NOT NULL DEFAULT '',
  consequence_oneliner TEXT NOT NULL DEFAULT '',
  verbatim_text TEXT
);

CREATE TABLE IF NOT EXISTS relationship_ledger (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  npc_id TEXT NOT NULL DEFAULT '',
  player_id TEXT NOT NULL DEFAULT '',
  turn INTEGER NOT NULL DEFAULT 0,
  delta REAL NOT NULL DEFAULT 0.0,
  reason TEXT NOT NULL DEFAULT '',
  category TEXT NOT NULL DEFAULT 'trust',
  decay_class TEXT NOT NULL DEFAULT 'slow'
);

CREATE TABLE IF NOT EXISTS moods (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  npc_id TEXT NOT NULL UNIQUE,
  valence REAL NOT NULL DEFAULT 0.0,
  arousal REAL NOT NULL DEFAULT 0.0,
  baseline_valence REAL NOT NULL DEFAULT 0.0,
  baseline_arousal REAL NOT NULL DEFAULT 0.0,
  last_updated_turn INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS saga_levels (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  level TEXT NOT NULL DEFAULT 'session',
  scope_id TEXT NOT NULL DEFAULT '',
  text TEXT NOT NULL DEFAULT '',
  created_turn INTEGER NOT NULL DEFAULT 0,
  references TEXT NOT NULL DEFAULT '[]'
);

CREATE TABLE IF NOT EXISTS telemetry (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  turn INTEGER NOT NULL DEFAULT 0,
  provider TEXT NOT NULL DEFAULT '',
  model TEXT NOT NULL DEFAULT '',
  prompt_tokens INTEGER NOT NULL DEFAULT 0,
  completion_tokens INTEGER NOT NULL DEFAULT 0,
  budget_alloc TEXT NOT NULL DEFAULT '{}',
  dropped TEXT NOT NULL DEFAULT '[]',
  notes TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS meta (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS provider_caps (
  provider_key TEXT PRIMARY KEY,
  caps TEXT NOT NULL DEFAULT '{}',
  probed_at INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_npc_memory_npc ON npc_memory(npc_id);
CREATE INDEX IF NOT EXISTS idx_chronicle_turn ON chronicle(turn_id);
CREATE INDEX IF NOT EXISTS idx_ledger_npc ON relationship_ledger(npc_id, player_id);
CREATE INDEX IF NOT EXISTS idx_facts_pinned ON world_facts(pinned);
CREATE INDEX IF NOT EXISTS idx_turn_log_turn ON turn_log(turn);
CREATE INDEX IF NOT EXISTS idx_saga_level ON saga_levels(level, scope_id);
