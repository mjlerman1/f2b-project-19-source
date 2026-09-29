-- BenchLink revision 3: team divisions and the captain-on-link marker from the roster
-- upload. Additive only: existing teams get division '' and existing players get
-- captain_on_link 0; no row is updated, deleted or rebuilt.
ALTER TABLE teams ADD COLUMN division TEXT NOT NULL DEFAULT '';
ALTER TABLE players ADD COLUMN captain_on_link INTEGER NOT NULL DEFAULT 0 CHECK (captain_on_link IN (0,1));
