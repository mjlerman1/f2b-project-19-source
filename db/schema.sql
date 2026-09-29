-- Generated from a fresh database after all kit and app migrations. Review only.
CREATE TABLE games (
  id INTEGER PRIMARY KEY,
  group_id INTEGER NOT NULL REFERENCES user_groups(id) ON DELETE CASCADE,
  home_team_id INTEGER NOT NULL REFERENCES teams(id),
  away_team_id INTEGER NOT NULL REFERENCES teams(id),
  starts_at TEXT NOT NULL,
  rink TEXT NOT NULL DEFAULT '',
  created_at INTEGER NOT NULL,
  CHECK (home_team_id <> away_team_id)
);
CREATE TABLE memberships (group_id INTEGER NOT NULL REFERENCES user_groups(id) ON DELETE CASCADE,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  role TEXT NOT NULL CHECK (role IN ('owner', 'admin', 'member')), created_at INTEGER NOT NULL,
  PRIMARY KEY (group_id, user_id));
CREATE TABLE players (
  id INTEGER PRIMARY KEY,
  group_id INTEGER NOT NULL REFERENCES user_groups(id) ON DELETE CASCADE,
  team_id INTEGER NOT NULL REFERENCES teams(id),
  first_name TEXT NOT NULL,
  last_name TEXT NOT NULL DEFAULT '',
  jersey TEXT NOT NULL DEFAULT '',
  is_goalie INTEGER NOT NULL DEFAULT 0 CHECK (is_goalie IN (0, 1)),
  external_id TEXT NOT NULL DEFAULT '',
  email TEXT NOT NULL DEFAULT '',
  phone TEXT NOT NULL DEFAULT '',
  phone_source TEXT NOT NULL DEFAULT '' CHECK (phone_source IN ('', 'import', 'self')),
  available_as_sub INTEGER NOT NULL DEFAULT 0 CHECK (available_as_sub IN (0, 1)),
  user_id INTEGER REFERENCES users(id),
  created_at INTEGER NOT NULL
, preferred_positions TEXT NOT NULL DEFAULT '', captain_on_link INTEGER NOT NULL DEFAULT 0 CHECK (captain_on_link IN (0,1)));
CREATE TABLE roster_imports (
  id INTEGER PRIMARY KEY,
  group_id INTEGER NOT NULL REFERENCES user_groups(id) ON DELETE CASCADE,
  status TEXT NOT NULL CHECK (status IN ('preview', 'committed')),
  filename TEXT NOT NULL DEFAULT '',
  csv_text TEXT NOT NULL,
  mapping_json TEXT NOT NULL DEFAULT '{}',
  summary_json TEXT NOT NULL DEFAULT '{}',
  created_by INTEGER NOT NULL REFERENCES users(id),
  created_at INTEGER NOT NULL,
  committed_at INTEGER
);
CREATE TABLE rsvps (
  id INTEGER PRIMARY KEY,
  group_id INTEGER NOT NULL REFERENCES user_groups(id) ON DELETE CASCADE,
  game_id INTEGER NOT NULL REFERENCES games(id) ON DELETE CASCADE,
  player_id INTEGER NOT NULL REFERENCES players(id),
  status TEXT NOT NULL CHECK (status IN ('in', 'out')),
  set_by INTEGER NOT NULL REFERENCES users(id),
  updated_at INTEGER NOT NULL,
  UNIQUE (game_id, player_id)
);
CREATE TABLE schema_version (component TEXT NOT NULL, version INTEGER NOT NULL, name TEXT NOT NULL, sha256 TEXT NOT NULL, applied_at INTEGER NOT NULL, PRIMARY KEY (component, version));
CREATE TABLE sessions (token_sha256 TEXT PRIMARY KEY, user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
  csrf_token TEXT NOT NULL, created_at INTEGER NOT NULL, expires_at INTEGER NOT NULL);
CREATE TABLE sub_requests (
  id INTEGER PRIMARY KEY,
  group_id INTEGER NOT NULL REFERENCES user_groups(id) ON DELETE CASCADE,
  game_id INTEGER NOT NULL REFERENCES games(id) ON DELETE CASCADE,
  team_id INTEGER NOT NULL REFERENCES teams(id),
  player_id INTEGER NOT NULL REFERENCES players(id),
  status TEXT NOT NULL CHECK (status IN ('pending', 'confirmed', 'declined')),
  requested_by INTEGER NOT NULL REFERENCES users(id),
  home_approved_by INTEGER REFERENCES users(id),
  away_approved_by INTEGER REFERENCES users(id),
  created_at INTEGER NOT NULL,
  decided_at INTEGER
);
CREATE TABLE team_captains (
  id INTEGER PRIMARY KEY,
  group_id INTEGER NOT NULL REFERENCES user_groups(id) ON DELETE CASCADE,
  team_id INTEGER NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
  user_id INTEGER NOT NULL REFERENCES users(id),
  created_at INTEGER NOT NULL,
  UNIQUE (team_id, user_id)
);
CREATE TABLE teams (
  id INTEGER PRIMARY KEY,
  group_id INTEGER NOT NULL REFERENCES user_groups(id) ON DELETE CASCADE,
  name TEXT NOT NULL,
  name_key TEXT NOT NULL,
  created_at INTEGER NOT NULL, division TEXT NOT NULL DEFAULT '',
  UNIQUE (group_id, name_key)
);
CREATE TABLE user_groups (id INTEGER PRIMARY KEY, name TEXT NOT NULL,
  created_by INTEGER NOT NULL REFERENCES users(id), created_at INTEGER NOT NULL);
CREATE TABLE users (id INTEGER PRIMARY KEY, username TEXT NOT NULL UNIQUE, password_hash TEXT NOT NULL,
  created_at INTEGER NOT NULL, failed_signins INTEGER NOT NULL DEFAULT 0, locked_until INTEGER NOT NULL DEFAULT 0);
CREATE INDEX games_group ON games(group_id, starts_at);
CREATE INDEX memberships_user ON memberships(user_id);
CREATE INDEX players_team ON players(group_id, team_id);
CREATE UNIQUE INDEX players_user ON players(group_id, user_id) WHERE user_id IS NOT NULL;
CREATE INDEX roster_imports_group ON roster_imports(group_id);
CREATE INDEX rsvps_game ON rsvps(group_id, game_id);
CREATE INDEX sessions_user ON sessions(user_id);
CREATE INDEX sub_requests_game ON sub_requests(group_id, game_id);
CREATE INDEX sub_requests_player ON sub_requests(group_id, player_id);
CREATE INDEX team_captains_group ON team_captains(group_id, user_id);
CREATE INDEX teams_group ON teams(group_id);
