-- BenchLink revision 2: players can list preferred positions (comma-separated codes
-- in the canonical order C, LW, RW, D, G). Additive only: existing rows keep their values
-- and get the empty default.
ALTER TABLE players ADD COLUMN preferred_positions TEXT NOT NULL DEFAULT '';
