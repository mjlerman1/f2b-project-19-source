# Backup and restore

All data is in `$APP_DATA_DIR/app.db` (plus `app.db-wal` and `app.db-shm` while
running). Back up with SQLite's online backup API (for example
`sqlite3 app.db ".backup backup.db"`), or stop the app and copy all three files.
Backups contain players' phone numbers and email addresses: store them
encrypted with access limited to operators.

Restore: stop the app, put the backup in place as `app.db` in an empty data
directory, start the app and check `/health`, then sign in and read a league's
teams, players, games and sub requests. Test restores in an isolated target
first.
