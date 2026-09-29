# Testing

Builder tests: from the app root run

    python -B -m unittest discover tests

They use the kit's in-process `appkit.Client` with a temporary data directory,
`tests/sample_sportsengine_roster.txt` (a fictional SportsEngine roster
export in CSV form, saved as `.txt`) and `tests/sample_league_roster_upload.txt`
(a fictional roster CSV saved from Excel with division and captain columns).
They cover the import preview, commit, duplicate and header-mapping rules, phone self-service, games and available
subs, the eight-skater limit, two-captain approval, the contact-reveal rule,
sub eligibility, roles, league isolation, the screens and persistence across a
reopen of the same data directory. For revision 2 they also cover preferred
positions (canonical order, refusal of unknown or repeated codes, imports
leaving the field alone) and an upgrade of a database created with only the v1
migration: every existing row is kept and the applied app migrations match the
files in `db/migrations/`. For revision 3 they cover the Excel roster upload
(divisions, "team in two divisions", "division too long", captain cells and
the "captain cell not recognised" warning), captain on link from both
triggers, `PATCH /teams/<tid>` divisions, the division-grouped screens, the
per-viewer contact card, and an upgrade of a revision 2 database that keeps
every row, preferred positions and captains before a re-import adds divisions
and a captain. Each test class names the acceptance id it exercises.

These tests are the builder's own claims. Independent acceptance is performed
only by the operator's harness with an operator-owned check spec.
