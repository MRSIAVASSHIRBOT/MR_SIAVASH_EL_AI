-- Table: _cf_KV
CREATE TABLE _cf_KV (
        key TEXT PRIMARY KEY,
        value BLOB
      ) WITHOUT ROWID;

-- Table: bridge_nonces
CREATE TABLE bridge_nonces (
    nonce_hash TEXT PRIMARY KEY,
    expires_at INTEGER NOT NULL
);

-- Table: telegram_jobs
CREATE TABLE telegram_jobs (
    job_id TEXT PRIMARY KEY,
    update_id INTEGER NOT NULL UNIQUE,
    chat_id INTEGER NOT NULL,
    telegram_user_id INTEGER NOT NULL,
    payload_ciphertext TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('pending', 'leased', 'sending', 'done')),
    lease_until INTEGER NOT NULL DEFAULT 0,
    lease_token_hash TEXT,
    attempts INTEGER NOT NULL DEFAULT 0,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);

-- Table: telegram_processed_updates
CREATE TABLE telegram_processed_updates (
  update_id INTEGER PRIMARY KEY,
  state TEXT NOT NULL CHECK (state IN ('processing', 'retry', 'done')),
  lease_until INTEGER NOT NULL,
  processed_at INTEGER
);

-- Table: telegram_sessions
CREATE TABLE telegram_sessions (
  client_id TEXT PRIMARY KEY,
  telegram_user_id INTEGER NOT NULL,
  credential_ciphertext TEXT NOT NULL,
  access_expires_at INTEGER NOT NULL,
  scopes_json TEXT NOT NULL,
  updated_at INTEGER NOT NULL
);
