# Authorization

Model: group-scoped. A league is one kit group; every BenchLink table has
`group_id` and all reads and writes go through `req.group(gid)` / `GroupScope`
or raw SQL that filters on `group_id` in every statement. Every id from a URL or
request body is looked up inside the league first: a foreign URL id is 404, a
foreign body id is 400 ("Unknown team"/"Unknown player").

Roles (enforced on the server):

- Anonymous API requests: 401. Pages redirect to `/signin`.
- Members of other leagues: 404 for every league path.
- League admin (kit `owner` or `admin`): add members (kit route), import
  rosters, create/edit/delete teams and players, assign captains, link and
  unlink accounts, create games. Wrong role: 403.
- Captain (member listed in `team_captains`): sets RSVPs for their team's
  players, requests subs for their team, approves or declines requests for
  games their team plays. Being a league admin does not make you a captain.
- Player (member linked to a `players` row): reads and edits their own phone,
  `available_as_sub` and `preferred_positions` with `PATCH /me/player` and
  sets their own RSVP. Nobody else can set a player's preferred positions
  (`PATCH /players/<pid>` refuses the key with 400); they are not contact
  details, so every member sees them in player objects and available subs.
- Captain on link (revision 3): a roster import can mark a player
  `captain_on_link`. A marked player who has a linked account becomes a captain
  of the team they are on at that moment, when an admin links the account
  (`POST /players/<pid>/link`) or when an import marking an already-linked
  player is committed. The grant is `INSERT OR IGNORE` into `team_captains`.
  Only league admins import and link, so the rule grants nothing an admin did
  not set up. Imports never remove captain rights and never grant them to an
  unlinked player; unlinking or moving the player does not remove them. Admins
  still add and remove captains by hand.
- Team divisions (revision 3) are set by admin imports or
  `PATCH /teams/<tid>` and never affect who may sub, request or approve.
- A sub is confirmed only when a captain of each team in the game approves; one
  person cannot approve both sides; eligibility and the eight-skater limit are
  re-checked on the confirming approval.

Contact details (phone, email) appear only in the player's own `/me`,
`PATCH /me/player` and `GET /players/<own id>` responses and `/me` page, and in
the `contact` of a confirmed sub request for the captains of both game teams and
the sub (API and game desk). On the game desk the card shows each viewer the
other people only: a game captain sees their sub and the other captains, the
sub sees the captains of both teams, and nobody sees their own details there.
No list, preview, error message or log line ever
contains them, including for league admins. Admins cannot set phone or email.

Every POST, PUT, PATCH and DELETE needs the session's CSRF token (kit).
