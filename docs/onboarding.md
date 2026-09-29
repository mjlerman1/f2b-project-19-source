# Onboarding and import

1. A league admin creates an account, creates a league ("New league" on `/`)
   and adds members by username (kit route `POST /api/groups/<gid>/members`).
2. On the roster page the admin chooses a SportsEngine roster export (CSV) or
   pastes its text, optionally with a header mapping (JSON object from header to
   field or null). `POST /api/groups/<gid>/imports` returns a preview: column
   mapping, ignored columns, and per row the action (create, update, unchanged,
   duplicate, error), name, team, jersey, goalie flag and phone status
   (present, missing, invalid). Nothing is saved yet and the preview never
   shows phone numbers or email addresses.
3. "Commit import" (`POST .../imports/<iid>/commit`) applies it once; a second
   commit is 409. Unknown teams are created. Re-importing the same file creates
   no duplicate players, and an import never overwrites a stored phone number.
4. The admin assigns captains and links member accounts to players. Players
   then add or change their phone on their profile page and choose whether they
   are available to sub. There is no SMS verification in v1.

Roster kept in Excel (revision 3): keep one row per player with first name,
last name, phone, team, division (for example Upper or Lower) and a captain
column, then use File → Save As → CSV UTF-8 and upload the file. Headers such as
`athlete_1_first_name`, `athlete_1_last_name`, `Cell Phone`, `Team`, `League`
and `Captain` are recognised with no mapping (`division` and `captain` are also
valid mapping values). Only CSV is read; there is no `.xlsx` upload. The
preview shows each row's division and captain mark. A team whose rows name two
different divisions, or a division over 40 characters, is an error row. On
commit each team takes the division of its first committed row with one (a
blank cell leaves it unchanged), and a player marked captain (`y`, `yes`,
`true`, `1`, `x`, `c`, `captain`) becomes a captain of their team once their
account is linked. A blank or unrecognised cell never removes a mark or a
captaincy. Admins can also set a team's division on the roster page.

Recognised headers, the mapping rules and row rules are those of the approved
BenchLink plan (section 5, extended by revision 3 in section 11); see
`src/roster_import.py`.
