# Persistence

All state lives in SQLite at `$APP_DATA_DIR/app.db` (WAL mode,
`synchronous=FULL`): accounts, sessions, leagues and memberships (kit tables),
teams, captains, players and account links, games, RSVPs, sub requests and
roster imports. Nothing is kept only in memory. Every request runs in one
transaction, so a killed process loses at most the request in flight and
committed rosters, captains, links, games, RSVPs, imports and confirmed subs
survive a restart.

An uploaded roster CSV is stored in `roster_imports.csv_text` only while the
import is a preview; committing empties it and keeps just the summary.

Keep the data directory on a dedicated durable volume, separate from the code.
