# Migrations

App migrations live in `db/migrations/NNNN_name.sql`, numbered from 0001 with
no gaps. There are three:

- `0001_benchlink.sql` (v1, unchanged byte for byte) creates `teams`,
  `team_captains`, `players`, `games`, `rsvps`, `sub_requests` and
  `roster_imports` (every table has `group_id` for its league) plus indexes.
- `0002_preferred_positions.sql` (revision 2) runs
  `ALTER TABLE players ADD COLUMN preferred_positions TEXT NOT NULL DEFAULT '';`.
  It only adds a column with a default: existing players get `''` (shown as
  `[]`) and no row is updated, deleted or rebuilt.
- `0003_divisions_and_captains.sql` (revision 3) runs
  `ALTER TABLE teams ADD COLUMN division TEXT NOT NULL DEFAULT '';` and
  `ALTER TABLE players ADD COLUMN captain_on_link INTEGER NOT NULL DEFAULT 0 CHECK (captain_on_link IN (0,1));`.
  Existing teams get `''` (no division) and existing players `0`; no row is
  updated, deleted or rebuilt. `0001` and `0002` stay byte-identical.

The kit applies pending files at startup in one transaction and records each in
`schema_version` with its SHA-256. An applied file must never change; the app
refuses to start on an edited, missing or unknown migration or a database newer
than the code. Files contain no `BEGIN`/`COMMIT`/`PRAGMA`/`ATTACH`.

Changes are forward-only and additive: add a new numbered file that adds
columns with defaults, tables or indexes. Never drop, rebuild or rewrite rows.

Upgrading from revision 2 (or v1): stop the running version, replace the code
directory with revision 3 and start it on the same `APP_DATA_DIR`. At startup
the kit verifies the SHA-256 of every applied file, applies the pending ones
(`0003`, plus `0002` when coming from v1) in one transaction and records them
in `schema_version`. Teams keep their captains and players keep their phone,
availability and preferred positions; divisions and captain marks arrive with
the next roster import (or a division set on the roster page). Back up `app.db`
first (see `backup.md`); there is no downgrade path, because older code refuses
to start on a database with a newer migration applied, so roll back by
restoring that backup.

`db/schema.sql` is the resulting schema from a fresh database, for review only;
a builder test checks it matches the migrations.
