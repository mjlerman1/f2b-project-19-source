"""BenchLink (revision 3): a substitute finder for adult hockey leagues, built on the Fund2Build app kit v1.

A league is a kit group. League admins (kit owner/admin) import SportsEngine roster
CSV exports, manage teams, captains, account links and games. Captains request and
approve subs; players keep their own phone number and sub availability up to date.
Contact details are shown only to the player themself and, after both game captains
confirm a sub, to the game captains and the sub. BenchLink never sends messages.
Revision 2 adds preferred positions (C, LW, RW, D, G) that players set for themselves.
Revision 3 adds team divisions and the captain-on-link marker from the roster upload
(a CSV saved from Excel), and shows each viewer of the game desk's contact card only
the other people.
"""
import datetime
import json
import re
import time
from pathlib import Path

import appkit
import roster_import
from appkit import HTTPError, Response, h, json_response, page, redirect

ROOT = Path(__file__).resolve().parent.parent
app = appkit.App("BenchLink", ROOT / "db" / "migrations", max_body=2 * 1024 * 1024)
ADMIN_ROLES = ("owner", "admin")
SKATER_LIMIT = 8
MAX_NAME = 80
MAX_JERSEY = 16
MAX_RINK = 120
MAX_DIVISION = 40
POSITIONS = ("C", "LW", "RW", "D", "G")  # canonical order of preferred-position codes
STARTS_AT = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}\Z")
STATIC_JS = (Path(__file__).resolve().parent / "static" / "benchlink.js").read_text("utf-8")


# ---- small helpers -----------------------------------------------------------------

def now():
    return int(time.time())


def utc_now_minute():
    return time.strftime("%Y-%m-%dT%H:%M", time.gmtime())


def name_key(name):
    return " ".join(name.split()).lower()


def is_admin(scope):
    return scope.role in ADMIN_ROLES


def body(req):
    return req.json()


def check_keys(data, allowed):
    unknown = sorted(set(data) - set(allowed))
    if unknown:
        raise HTTPError(400, "Unknown field: " + ", ".join(unknown)[:200])


def parse_id(value, field):
    if isinstance(value, bool):
        raise HTTPError(400, f"{field} must be an integer id")
    if isinstance(value, int):
        return value
    if isinstance(value, str) and re.fullmatch(r"[0-9]{1,30}", value):
        return int(value)
    raise HTTPError(400, f"{field} must be an integer id")


def body_row(scope, table, value, field, label):
    row_id = parse_id(value, field)
    row = scope.find(table, row_id) if 0 < row_id < 2 ** 63 else None
    if row is None:
        raise HTTPError(400, f"Unknown {label}")
    return row


def text_field(data, key, *, required, max_len, label, allow_empty=False, default=""):
    if key not in data or data[key] is None and not required:
        if required:
            raise HTTPError(400, f"{label} is required")
        return default
    value = data[key]
    if isinstance(value, int) and not isinstance(value, bool) and key == "jersey":
        value = str(value)
    if not isinstance(value, str):
        raise HTTPError(400, f"{label} must be a string")
    value = value.strip()
    if (not value and not allow_empty) or len(value) > max_len:
        raise HTTPError(400, f"{label} must be {0 if allow_empty else 1}-{max_len} characters")
    return value


def bool_field(data, key):
    value = data[key]
    if not isinstance(value, bool):
        raise HTTPError(400, f"{key} must be true or false")
    return value


def team_name_from(data):
    name = data.get("name")
    if not isinstance(name, str) or not 1 <= len(name.strip()) <= MAX_NAME:
        raise HTTPError(400, "Team name must be 1-80 characters")
    return name.strip()


def member_by_username(scope, username):
    if not isinstance(username, str) or not username:
        raise HTTPError(400, "username is required")
    row = scope.db.execute("SELECT u.id, u.username FROM users u JOIN memberships m ON m.user_id=u.id "
                           "WHERE m.group_id=? AND u.username=?", (scope.group_id, username)).fetchone()
    if not row:
        raise HTTPError(404, "No league member with that username")
    return row


# ---- reads ------------------------------------------------------------------------

PLAYER_SELECT = ("SELECT p.*, t.name AS team_name, t.name_key AS team_key FROM players p "
                 "JOIN teams t ON t.id=p.team_id AND t.group_id=p.group_id WHERE p.group_id=?")


def load_player(scope, player_id):
    row = scope.db.execute(PLAYER_SELECT + " AND p.id=?", (scope.group_id, player_id)).fetchone()
    if row is None:
        raise HTTPError(404, "Not found")
    return row


def my_player(scope):
    return scope.db.execute(PLAYER_SELECT + " AND p.user_id=?", (scope.group_id, scope.user_id)).fetchone()


def full_name(p):
    return (p["first_name"] + " " + p["last_name"]).strip()


def positions_list(stored):
    """Stored comma-separated codes as a list in canonical order."""
    codes = set((stored or "").split(","))
    return [code for code in POSITIONS if code in codes]


def parse_positions(value):
    """A request's preferred_positions: a list of distinct codes, stored in canonical order."""
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise HTTPError(400, "preferred_positions must be a list of position codes")
    if len(set(value)) != len(value):
        raise HTTPError(400, "preferred_positions must not repeat a code")
    unknown = [v for v in value if v not in POSITIONS]
    if unknown:
        raise HTTPError(400, "preferred_positions codes must be C, LW, RW, D or G")
    return ",".join(code for code in POSITIONS if code in value)


def player_json(p, self_view=False):
    data = {"id": p["id"], "team_id": p["team_id"], "team_name": p["team_name"], "first_name": p["first_name"],
            "last_name": p["last_name"], "name": full_name(p), "jersey": p["jersey"],
            "is_goalie": bool(p["is_goalie"]), "available_as_sub": bool(p["available_as_sub"]),
            "linked": p["user_id"] is not None, "has_phone": bool(p["phone"]),
            "preferred_positions": positions_list(p["preferred_positions"]),
            "captain_on_link": bool(p["captain_on_link"])}
    if self_view:
        data["phone"], data["email"] = p["phone"], p["email"]
    return data


def player_for_caller(scope, p):
    return player_json(p, self_view=p["user_id"] is not None and p["user_id"] == scope.user_id)


def team_captains(scope, team_id):
    return scope.db.execute("SELECT tc.user_id, u.username FROM team_captains tc JOIN users u ON u.id=tc.user_id "
                            "WHERE tc.group_id=? AND tc.team_id=? ORDER BY u.username",
                            (scope.group_id, team_id)).fetchall()


def team_json(scope, team):
    count = scope.db.execute("SELECT COUNT(*) FROM players WHERE group_id=? AND team_id=?",
                             (scope.group_id, team["id"])).fetchone()[0]
    return {"id": team["id"], "name": team["name"],
            "captains": [{"user_id": c["user_id"], "username": c["username"]} for c in team_captains(scope, team["id"])],
            "player_count": count, "division": team["division"]}


def captain_team_ids(scope, user_id=None):
    return [r["team_id"] for r in scope.db.execute(
        "SELECT DISTINCT team_id FROM team_captains WHERE group_id=? AND user_id=? ORDER BY team_id",
        (scope.group_id, scope.user_id if user_id is None else user_id))]


def is_captain(scope, team_id):
    return scope.db.execute("SELECT 1 FROM team_captains WHERE group_id=? AND team_id=? AND user_id=?",
                            (scope.group_id, team_id, scope.user_id)).fetchone() is not None


def side_count(scope, game_id, team_id):
    db, gid = scope.db, scope.group_id

    def count(sql, *params):
        return db.execute(sql, (gid, gid, game_id, team_id, *params)).fetchone()[0]
    rsvp = ("SELECT COUNT(*) FROM rsvps r JOIN players p ON p.id=r.player_id AND p.group_id=? "
            "WHERE r.group_id=? AND r.game_id=? AND p.team_id=? AND r.status=?")
    rsvp_in = count(rsvp + " AND p.is_goalie=0", "in")
    rsvp_out = count(rsvp, "out")
    goalies_in = count(rsvp + " AND p.is_goalie=1", "in")
    subs = count("SELECT COUNT(*) FROM sub_requests s JOIN players p ON p.id=s.player_id AND p.group_id=? "
                 "WHERE s.group_id=? AND s.game_id=? AND s.team_id=? AND s.status='confirmed' AND p.is_goalie=0")
    return {"rsvp_in": rsvp_in, "rsvp_out": rsvp_out, "goalies_in": goalies_in, "confirmed_subs": subs,
            "count": rsvp_in + subs, "limit": SKATER_LIMIT}


def game_json(scope, game):
    home, away = scope.get("teams", game["home_team_id"]), scope.get("teams", game["away_team_id"])
    return {"id": game["id"], "home_team": {"id": home["id"], "name": home["name"]},
            "away_team": {"id": away["id"], "name": away["name"]}, "starts_at": game["starts_at"],
            "rink": game["rink"], "skaters": {"home": side_count(scope, game["id"], home["id"]),
                                              "away": side_count(scope, game["id"], away["id"])}}


def upcoming_games(scope, include_past=False):
    if include_past:
        sql, params = "SELECT * FROM games WHERE group_id=? ORDER BY starts_at, id", (scope.group_id,)
    else:
        sql = "SELECT * FROM games WHERE group_id=? AND starts_at>=? ORDER BY starts_at, id"
        params = (scope.group_id, utc_now_minute())
    return scope.db.execute(sql, params).fetchall()


def captains_game(scope, game):
    """Which sides of the game the caller captains: subset of {'home', 'away'}."""
    sides = set()
    if is_captain(scope, game["home_team_id"]):
        sides.add("home")
    if is_captain(scope, game["away_team_id"]):
        sides.add("away")
    return sides


def available_subs(scope, game):
    return scope.db.execute(
        PLAYER_SELECT + " AND p.available_as_sub=1 AND p.team_id NOT IN (?, ?) AND NOT EXISTS ("
        "SELECT 1 FROM sub_requests s WHERE s.group_id=p.group_id AND s.game_id=? AND s.player_id=p.id "
        "AND s.status IN ('pending', 'confirmed')) ORDER BY p.last_name, p.first_name, p.id",
        (scope.group_id, game["home_team_id"], game["away_team_id"], game["id"])).fetchall()


def may_see_contact(scope, sr, game, sub):
    if sr["status"] != "confirmed":
        return False
    return bool(captains_game(scope, game)) or (sub["user_id"] is not None and sub["user_id"] == scope.user_id)


def contact_json(scope, game, sub):
    captains = []
    for team_id in (game["home_team_id"], game["away_team_id"]):
        team = scope.get("teams", team_id)
        for cap in team_captains(scope, team_id):
            linked = scope.db.execute("SELECT * FROM players WHERE group_id=? AND user_id=?",
                                      (scope.group_id, cap["user_id"])).fetchone()
            captains.append({"team_id": team_id, "team_name": team["name"], "username": cap["username"],
                             "name": full_name(linked) if linked else "",
                             "phone": linked["phone"] if linked else "", "email": linked["email"] if linked else ""})
    return {"sub": {"name": full_name(sub), "phone": sub["phone"], "email": sub["email"]}, "captains": captains}


def sub_request_json(scope, sr, with_contact=False):
    team = scope.get("teams", sr["team_id"])
    sub = load_player(scope, sr["player_id"])
    data = {"id": sr["id"], "game_id": sr["game_id"], "team_id": sr["team_id"], "team_name": team["name"],
            "player": {"id": sub["id"], "name": full_name(sub), "team_id": sub["team_id"],
                       "team_name": sub["team_name"]},
            "status": sr["status"],
            "approvals": {"home": sr["home_approved_by"] is not None, "away": sr["away_approved_by"] is not None},
            "created_at": sr["created_at"]}
    if with_contact:
        game = scope.get("games", sr["game_id"])
        if may_see_contact(scope, sr, game, sub):
            data["contact"] = contact_json(scope, game, sub)
    return data


def eligible(scope, game, player):
    return bool(player["available_as_sub"]) and player["team_id"] not in (game["home_team_id"], game["away_team_id"])


# ---- league profile and phone self-service ------------------------------------------------

@app.route("GET", "/api/groups/<int:gid>/me")
def league_me(req, gid):
    scope = req.group(gid)
    league = req.db.execute("SELECT id, name FROM user_groups WHERE id=?", (gid,)).fetchone()
    player = my_player(scope)
    assignments = []
    if player:
        assignments = [{"id": r["id"], "game_id": r["game_id"], "status": r["status"]} for r in scope.db.execute(
            "SELECT id, game_id, status FROM sub_requests WHERE group_id=? AND player_id=? ORDER BY id",
            (gid, player["id"]))]
    return json_response({"league": {"id": league["id"], "name": league["name"]}, "role": scope.role,
                          "is_league_admin": is_admin(scope), "captain_of": captain_team_ids(scope),
                          "player": player_json(player, self_view=True) if player else None,
                          "sub_assignments": assignments})


@app.route("PATCH", "/api/groups/<int:gid>/me/player")
def update_my_player(req, gid):
    scope = req.group(gid)
    player = my_player(scope)
    if player is None:
        raise HTTPError(404, "Your account is not linked to a player in this league")
    data = body(req)
    check_keys(data, ("phone", "available_as_sub", "preferred_positions"))
    changes = {}
    phone = player["phone"]
    if "phone" in data:
        if not isinstance(data["phone"], str):
            raise HTTPError(400, "phone must be a string")
        if data["phone"].strip() == "":
            phone = ""
            changes.update(phone="", phone_source="", available_as_sub=0)
        else:
            phone = roster_import.normalize_phone(data["phone"])
            if phone is None:
                raise HTTPError(400, "Enter a 10-digit North American phone number")
            changes.update(phone=phone, phone_source="self")
    if "available_as_sub" in data:
        wanted = bool_field(data, "available_as_sub")
        if wanted and not phone:
            raise HTTPError(400, "Add a phone number before opting in to sub")
        changes["available_as_sub"] = int(wanted)
    if "preferred_positions" in data:
        changes["preferred_positions"] = parse_positions(data["preferred_positions"])
    changes = {k: v for k, v in changes.items() if player[k] != v}
    if changes:
        scope.update("players", player["id"], **changes)
    return json_response({"player": player_json(load_player(scope, player["id"]), self_view=True)})


# ---- teams and captains --------------------------------------------------------------------

def ensure_unique_team(scope, key, except_id=None):
    row = scope.db.execute("SELECT id FROM teams WHERE group_id=? AND name_key=?", (scope.group_id, key)).fetchone()
    if row and row["id"] != except_id:
        raise HTTPError(409, "A team with that name already exists")


@app.route("GET", "/api/groups/<int:gid>/teams")
def list_teams(req, gid):
    scope = req.group(gid)
    return json_response({"teams": [team_json(scope, t) for t in scope.list("teams", order_by="name_key")]})


@app.route("POST", "/api/groups/<int:gid>/teams")
def create_team(req, gid):
    scope = req.group(gid, roles=ADMIN_ROLES)
    name = team_name_from(body(req))
    ensure_unique_team(scope, name_key(name))
    team_id = scope.insert("teams", name=name, name_key=name_key(name), created_at=now())
    return json_response({"team": team_json(scope, scope.get("teams", team_id))}, 201)


@app.route("GET", "/api/groups/<int:gid>/teams/<int:tid>")
def get_team(req, gid, tid):
    scope = req.group(gid)
    return json_response({"team": team_json(scope, scope.get("teams", tid))})


@app.route("PATCH", "/api/groups/<int:gid>/teams/<int:tid>")
def update_team(req, gid, tid):
    scope = req.group(gid, roles=ADMIN_ROLES)
    team = scope.get("teams", tid)
    data = body(req)
    check_keys(data, ("name", "division"))
    changes = {}
    if "name" in data:
        name = team_name_from(data)
        ensure_unique_team(scope, name_key(name), except_id=tid)
        if name != team["name"]:
            changes.update(name=name, name_key=name_key(name))
    if "division" in data:
        division = data["division"]
        if not isinstance(division, str) or len(" ".join(division.split())) > MAX_DIVISION:
            raise HTTPError(400, "division must be a string of at most 40 characters")
        division = " ".join(division.split())
        if division != team["division"]:
            changes["division"] = division
    if changes:
        team = scope.update("teams", tid, **changes)
    return json_response({"team": team_json(scope, team)})


@app.route("DELETE", "/api/groups/<int:gid>/teams/<int:tid>")
def delete_team(req, gid, tid):
    scope = req.group(gid, roles=ADMIN_ROLES)
    scope.get("teams", tid)
    db = scope.db
    used = (db.execute("SELECT 1 FROM players WHERE group_id=? AND team_id=?", (gid, tid)).fetchone() or
            db.execute("SELECT 1 FROM team_captains WHERE group_id=? AND team_id=?", (gid, tid)).fetchone() or
            db.execute("SELECT 1 FROM games WHERE group_id=? AND (home_team_id=? OR away_team_id=?)",
                       (gid, tid, tid)).fetchone())
    if used:
        raise HTTPError(409, "This team still has players, captains or games")
    scope.delete("teams", tid)
    return json_response({"deleted": tid})


@app.route("POST", "/api/groups/<int:gid>/teams/<int:tid>/captains")
def add_captain(req, gid, tid):
    scope = req.group(gid, roles=ADMIN_ROLES)
    team = scope.get("teams", tid)
    user = member_by_username(scope, body(req).get("username"))
    if scope.db.execute("SELECT 1 FROM team_captains WHERE group_id=? AND team_id=? AND user_id=?",
                        (gid, tid, user["id"])).fetchone():
        raise HTTPError(409, "That member is already a captain of this team")
    scope.insert("team_captains", team_id=tid, user_id=user["id"], created_at=now())
    return json_response({"team": team_json(scope, team)}, 201)


@app.route("DELETE", "/api/groups/<int:gid>/teams/<int:tid>/captains/<int:uid>")
def remove_captain(req, gid, tid, uid):
    scope = req.group(gid, roles=ADMIN_ROLES)
    team = scope.get("teams", tid)
    row = scope.db.execute("SELECT id FROM team_captains WHERE group_id=? AND team_id=? AND user_id=?",
                           (gid, tid, uid)).fetchone()
    if not row:
        raise HTTPError(404, "Not found")
    scope.delete("team_captains", row["id"])
    return json_response({"team": team_json(scope, team)})


# ---- players and account links ---------------------------------------------------------------

@app.route("GET", "/api/groups/<int:gid>/players")
def list_players(req, gid):
    scope = req.group(gid)
    sql, params = PLAYER_SELECT, [gid]
    if "team_id" in req.query:
        value = req.query["team_id"]
        if not re.fullmatch(r"[0-9]{1,18}", value):
            raise HTTPError(400, "team_id must be an integer id")
        scope.get("teams", int(value))
        sql += " AND p.team_id=?"
        params.append(int(value))
    rows = scope.db.execute(sql + " ORDER BY t.name_key, p.last_name, p.first_name, p.id", params).fetchall()
    return json_response({"players": [player_json(p) for p in rows]})


def player_fields(scope, data, creating):
    changes = {}
    if creating or "first_name" in data:
        changes["first_name"] = text_field(data, "first_name", required=True, max_len=MAX_NAME, label="first_name")
    if "last_name" in data:
        changes["last_name"] = text_field(data, "last_name", required=False, max_len=MAX_NAME, label="last_name",
                                          allow_empty=True)
    if "jersey" in data:
        changes["jersey"] = text_field(data, "jersey", required=False, max_len=MAX_JERSEY, label="jersey",
                                       allow_empty=True)
    if "is_goalie" in data:
        changes["is_goalie"] = int(bool_field(data, "is_goalie"))
    if creating or "team_id" in data:
        if "team_id" not in data:
            raise HTTPError(400, "team_id is required")
        changes["team_id"] = body_row(scope, "teams", data["team_id"], "team_id", "team")["id"]
    return changes


@app.route("POST", "/api/groups/<int:gid>/players")
def create_player(req, gid):
    scope = req.group(gid, roles=ADMIN_ROLES)
    data = body(req)
    check_keys(data, ("first_name", "last_name", "team_id", "jersey", "is_goalie"))
    fields = player_fields(scope, data, creating=True)
    player_id = scope.insert("players", created_at=now(), **fields)
    return json_response({"player": player_json(load_player(scope, player_id))}, 201)


@app.route("GET", "/api/groups/<int:gid>/players/<int:pid>")
def get_player(req, gid, pid):
    scope = req.group(gid)
    return json_response({"player": player_for_caller(scope, load_player(scope, pid))})


@app.route("PATCH", "/api/groups/<int:gid>/players/<int:pid>")
def update_player(req, gid, pid):
    scope = req.group(gid, roles=ADMIN_ROLES)
    player = load_player(scope, pid)
    data = body(req)
    check_keys(data, ("first_name", "last_name", "jersey", "is_goalie", "team_id"))
    changes = {k: v for k, v in player_fields(scope, data, creating=False).items() if player[k] != v}
    if changes:
        scope.update("players", pid, **changes)
    return json_response({"player": player_for_caller(scope, load_player(scope, pid))})


@app.route("POST", "/api/groups/<int:gid>/players/<int:pid>/link")
def link_player(req, gid, pid):
    scope = req.group(gid, roles=ADMIN_ROLES)
    player = load_player(scope, pid)
    user = member_by_username(scope, body(req).get("username"))
    if player["user_id"] is not None:
        raise HTTPError(409, "This player is already linked to an account")
    if scope.db.execute("SELECT 1 FROM players WHERE group_id=? AND user_id=?", (gid, user["id"])).fetchone():
        raise HTTPError(409, "That account is already linked to a player in this league")
    scope.update("players", pid, user_id=user["id"])
    roster_import.grant_captain_on_link(scope.db, gid, pid, now())
    return json_response({"player": player_for_caller(scope, load_player(scope, pid))})


@app.route("DELETE", "/api/groups/<int:gid>/players/<int:pid>/link")
def unlink_player(req, gid, pid):
    scope = req.group(gid, roles=ADMIN_ROLES)
    player = load_player(scope, pid)
    changes = {k: v for k, v in (("user_id", None), ("available_as_sub", 0)) if player[k] != v}
    if changes:
        scope.update("players", pid, **changes)
    return json_response({"player": player_json(load_player(scope, pid))})


# ---- roster imports --------------------------------------------------------------------------

def import_plan(scope, record):
    try:
        return roster_import.build_plan(scope.db, scope.group_id, record["csv_text"],
                                        json.loads(record["mapping_json"] or "{}"))
    except roster_import.RosterError as exc:
        raise HTTPError(400, str(exc)) from None


@app.route("GET", "/api/groups/<int:gid>/imports")
def list_imports(req, gid):
    scope = req.group(gid, roles=ADMIN_ROLES)
    rows = scope.db.execute("SELECT * FROM roster_imports WHERE group_id=? ORDER BY created_at DESC, id DESC",
                            (gid,)).fetchall()
    return json_response({"imports": [{"id": r["id"], "status": r["status"], "filename": r["filename"],
                                       "created_at": r["created_at"], "summary": json.loads(r["summary_json"])}
                                      for r in rows]})


@app.route("POST", "/api/groups/<int:gid>/imports")
def create_import(req, gid):
    scope = req.group(gid, roles=ADMIN_ROLES)
    data = body(req)
    text, mapping, filename = data.get("csv"), data.get("mapping"), data.get("filename", "")
    if not isinstance(text, str) or not text.strip():
        raise HTTPError(400, "csv must hold the text of the roster export")
    if filename is None:
        filename = ""
    if not isinstance(filename, str) or len(filename.strip()) > 200:
        raise HTTPError(400, "filename must be a string of at most 200 characters")
    try:
        plan = roster_import.build_plan(scope.db, gid, text, mapping)
        mapping_text = roster_import.mapping_json(mapping)
    except roster_import.RosterError as exc:
        raise HTTPError(400, str(exc)) from None
    import_id = scope.insert("roster_imports", status="preview", filename=filename.strip(), csv_text=text,
                             mapping_json=mapping_text, summary_json=json.dumps(plan.summary()),
                             created_by=scope.user_id, created_at=now())
    return json_response({"import": plan.preview(scope.get("roster_imports", import_id))}, 201)


@app.route("GET", "/api/groups/<int:gid>/imports/<int:iid>")
def get_import(req, gid, iid):
    scope = req.group(gid, roles=ADMIN_ROLES)
    record = scope.get("roster_imports", iid)
    if record["status"] == "committed":
        return json_response({"import": {"id": record["id"], "status": "committed", "filename": record["filename"],
                                         "summary": json.loads(record["summary_json"])}})
    return json_response({"import": import_plan(scope, record).preview(record)})


@app.route("POST", "/api/groups/<int:gid>/imports/<int:iid>/commit")
def commit_import(req, gid, iid):
    scope = req.group(gid, roles=ADMIN_ROLES)
    record = scope.get("roster_imports", iid)
    if record["status"] == "committed":
        raise HTTPError(409, "This import has already been committed")
    plan = import_plan(scope, record)
    summary = plan.summary()
    teams, rows = roster_import.commit_plan(scope.db, gid, plan)
    scope.update("roster_imports", iid, status="committed", csv_text="", summary_json=json.dumps(summary),
                 committed_at=now())
    return json_response({"import": {"id": iid, "status": "committed", "summary": summary},
                          "teams": teams, "rows": rows})


# ---- games and RSVPs -------------------------------------------------------------------------

def valid_starts_at(value):
    if not isinstance(value, str) or not STARTS_AT.fullmatch(value):
        return False
    try:
        datetime.datetime.strptime(value, "%Y-%m-%dT%H:%M")
    except ValueError:
        return False
    return True


@app.route("GET", "/api/groups/<int:gid>/games")
def list_games(req, gid):
    scope = req.group(gid)
    include_past = req.query.get("include_past") in ("1", "true")
    return json_response({"games": [game_json(scope, g) for g in upcoming_games(scope, include_past)]})


@app.route("POST", "/api/groups/<int:gid>/games")
def create_game(req, gid):
    scope = req.group(gid, roles=ADMIN_ROLES)
    data = body(req)
    home = body_row(scope, "teams", data.get("home_team_id"), "home_team_id", "team")
    away = body_row(scope, "teams", data.get("away_team_id"), "away_team_id", "team")
    if home["id"] == away["id"]:
        raise HTTPError(400, "A game needs two different teams")
    if not valid_starts_at(data.get("starts_at")):
        raise HTTPError(400, "starts_at must be a valid YYYY-MM-DDTHH:MM time")
    rink = data.get("rink", "")
    if rink is None:
        rink = ""
    if not isinstance(rink, str) or len(rink.strip()) > MAX_RINK:
        raise HTTPError(400, "rink must be at most 120 characters")
    game_id = scope.insert("games", home_team_id=home["id"], away_team_id=away["id"], starts_at=data["starts_at"],
                           rink=rink.strip(), created_at=now())
    return json_response({"game": game_json(scope, scope.get("games", game_id))}, 201)


@app.route("GET", "/api/groups/<int:gid>/games/<int:game_id>")
def get_game(req, gid, game_id):
    scope = req.group(gid)
    return json_response({"game": game_json(scope, scope.get("games", game_id))})


def game_rsvps(scope, game):
    return scope.db.execute(
        "SELECT r.player_id, r.status, p.first_name, p.last_name, p.team_id FROM rsvps r "
        "JOIN players p ON p.id=r.player_id AND p.group_id=r.group_id JOIN teams t ON t.id=p.team_id "
        "AND t.group_id=p.group_id WHERE r.group_id=? AND r.game_id=? "
        "ORDER BY t.name_key, p.last_name, p.first_name, p.id", (scope.group_id, game["id"])).fetchall()


@app.route("GET", "/api/groups/<int:gid>/games/<int:game_id>/rsvps")
def list_rsvps(req, gid, game_id):
    scope = req.group(gid)
    game = scope.get("games", game_id)
    return json_response({"rsvps": [{"player_id": r["player_id"], "name": full_name(r), "team_id": r["team_id"],
                                     "status": r["status"]} for r in game_rsvps(scope, game)]})


def may_set_rsvp(scope, player):
    return (is_admin(scope) or (player["user_id"] is not None and player["user_id"] == scope.user_id)
            or is_captain(scope, player["team_id"]))


@app.route("PUT", "/api/groups/<int:gid>/games/<int:game_id>/rsvps/<int:pid>")
def set_rsvp(req, gid, game_id, pid):
    scope = req.group(gid)
    game = scope.get("games", game_id)
    player = load_player(scope, pid)
    status = body(req).get("status")
    if status not in ("in", "out"):
        raise HTTPError(400, 'status must be "in" or "out"')
    if player["team_id"] not in (game["home_team_id"], game["away_team_id"]):
        raise HTTPError(400, "That player is not on either team in this game")
    if not may_set_rsvp(scope, player):
        raise HTTPError(403, "Only the player, their captain or a league admin can set this RSVP")
    current = scope.db.execute("SELECT * FROM rsvps WHERE group_id=? AND game_id=? AND player_id=?",
                               (gid, game_id, pid)).fetchone()
    if status == "in" and not player["is_goalie"] and not (current and current["status"] == "in"):
        if side_count(scope, game_id, player["team_id"])["count"] >= SKATER_LIMIT:
            raise HTTPError(409, "This team already has 8 skaters in for this game")
    if current is None:
        scope.insert("rsvps", game_id=game_id, player_id=pid, status=status, set_by=scope.user_id, updated_at=now())
    elif current["status"] != status:
        scope.update("rsvps", current["id"], status=status, set_by=scope.user_id, updated_at=now())
    return json_response({"rsvp": {"game_id": game_id, "player_id": pid, "status": status},
                          "game": game_json(scope, game)})


@app.route("GET", "/api/groups/<int:gid>/games/<int:game_id>/available-subs")
def list_available_subs(req, gid, game_id):
    scope = req.group(gid)
    game = scope.get("games", game_id)
    return json_response({"subs": [{"id": p["id"], "name": full_name(p), "team_id": p["team_id"],
                                    "team_name": p["team_name"], "jersey": p["jersey"],
                                    "is_goalie": bool(p["is_goalie"]),
                                    "preferred_positions": positions_list(p["preferred_positions"])}
                                   for p in available_subs(scope, game)]})


# ---- sub requests ------------------------------------------------------------------------------

def game_requests(scope, game_id):
    return scope.db.execute("SELECT * FROM sub_requests WHERE group_id=? AND game_id=? ORDER BY created_at, id",
                            (scope.group_id, game_id)).fetchall()


@app.route("GET", "/api/groups/<int:gid>/games/<int:game_id>/sub-requests")
def list_sub_requests(req, gid, game_id):
    scope = req.group(gid)
    scope.get("games", game_id)
    return json_response({"sub_requests": [sub_request_json(scope, r) for r in game_requests(scope, game_id)]})


@app.route("POST", "/api/groups/<int:gid>/games/<int:game_id>/sub-requests")
def create_sub_request(req, gid, game_id):
    scope = req.group(gid)
    game = scope.get("games", game_id)
    data = body(req)
    team_id = parse_id(data.get("team_id"), "team_id")
    if team_id == game["home_team_id"]:
        side = "home"
    elif team_id == game["away_team_id"]:
        side = "away"
    else:
        raise HTTPError(400, "team_id must be the home or away team of this game")
    if not is_captain(scope, team_id):
        raise HTTPError(403, "Only a captain of that team can request a sub")
    player = body_row(scope, "players", data.get("player_id"), "player_id", "player")
    if not eligible(scope, game, player):
        raise HTTPError(400, "That player is not eligible to sub in this game")
    if scope.db.execute("SELECT 1 FROM sub_requests WHERE group_id=? AND game_id=? AND player_id=? "
                        "AND status IN ('pending', 'confirmed')", (gid, game_id, player["id"])).fetchone():
        raise HTTPError(409, "That player already has an open request for this game")
    if not player["is_goalie"] and side_count(scope, game_id, team_id)["count"] >= SKATER_LIMIT:
        raise HTTPError(409, "This team already has 8 skaters in for this game")
    request_id = scope.insert("sub_requests", game_id=game_id, team_id=team_id, player_id=player["id"],
                              status="pending", requested_by=scope.user_id, created_at=now(),
                              **{f"{side}_approved_by": scope.user_id})
    return json_response({"sub_request": sub_request_json(scope, scope.get("sub_requests", request_id))}, 201)


@app.route("GET", "/api/groups/<int:gid>/sub-requests/<int:rid>")
def get_sub_request(req, gid, rid):
    scope = req.group(gid)
    return json_response({"sub_request": sub_request_json(scope, scope.get("sub_requests", rid), with_contact=True)})


@app.route("POST", "/api/groups/<int:gid>/sub-requests/<int:rid>/approve")
def approve_sub_request(req, gid, rid):
    scope = req.group(gid)
    sr = scope.get("sub_requests", rid)
    game = scope.get("games", sr["game_id"])
    sides = captains_game(scope, game)
    if not sides:
        raise HTTPError(403, "Only a captain of a team in this game can approve")
    if sr["status"] != "pending":
        raise HTTPError(409, "This request is no longer pending")
    if scope.user_id in (sr["home_approved_by"], sr["away_approved_by"]):
        raise HTTPError(409, "You have already approved this request")
    open_sides = [s for s in ("home", "away") if s in sides and sr[f"{s}_approved_by"] is None]
    if not open_sides:
        raise HTTPError(409, "Your side has already approved this request")
    changes = {f"{open_sides[0]}_approved_by": scope.user_id}
    other = "away" if open_sides[0] == "home" else "home"
    if sr[f"{other}_approved_by"] is not None:
        player = load_player(scope, sr["player_id"])
        if not eligible(scope, game, player):
            raise HTTPError(400, "That player is no longer eligible to sub in this game")
        if not player["is_goalie"] and side_count(scope, game["id"], sr["team_id"])["count"] >= SKATER_LIMIT:
            raise HTTPError(409, "This team already has 8 skaters in for this game")
        changes.update(status="confirmed", decided_at=now())
    sr = scope.update("sub_requests", rid, **changes)
    return json_response({"sub_request": sub_request_json(scope, sr)})


@app.route("POST", "/api/groups/<int:gid>/sub-requests/<int:rid>/decline")
def decline_sub_request(req, gid, rid):
    scope = req.group(gid)
    sr = scope.get("sub_requests", rid)
    game = scope.get("games", sr["game_id"])
    if not captains_game(scope, game):
        raise HTTPError(403, "Only a captain of a team in this game can decline")
    if sr["status"] != "pending":
        raise HTTPError(409, "This request is no longer pending")
    sr = scope.update("sub_requests", rid, status="declined", decided_at=now())
    return json_response({"sub_request": sub_request_json(scope, sr)})


# ---- screens ------------------------------------------------------------------------------------

@app.route("GET", "/static/benchlink.js")
def static_js(req):
    return Response(STATIC_JS, 200, "text/javascript")


def layout(req, title, content, gid=None):
    token = h(req.csrf_token())
    nav = ['<a href="/">My leagues</a>']
    if gid is not None:
        nav += [f'<a href="/leagues/{gid}">League</a>', f'<a href="/leagues/{gid}/roster">Roster</a>',
                f'<a href="/leagues/{gid}/me">My profile</a>']
    user = req.user["username"] if req.user else ""
    signout = (f'<form method="post" action="/api/signout"><input type="hidden" name="csrf_token" value="{token}">'
               f'<button>Sign out {h(user)}</button></form>')
    return page(title, f'<meta name="csrf-token" content="{token}"><header><strong>BenchLink</strong> '
                       f'<nav>{" | ".join(nav)}</nav>{signout}</header><main><h1>{h(title)}</h1>{content}</main>'
                       f'<p id="status" role="status"></p><script src="/static/benchlink.js"></script>')


def api_button(label, method, path, payload=None):
    data = f' data-body="{h(json.dumps(payload))}"' if payload is not None else ""
    return f'<button type="button" data-method="{h(method)}" data-path="{h(path)}"{data}>{h(label)}</button>'


def division_groups(teams):
    """Teams under a heading per division: Upper, Lower, other divisions alphabetically, then No division."""
    groups = {}
    for team in teams:
        key = team["division"].lower()
        groups.setdefault(key, (team["division"] or "No division", []))[1].append(team)

    def order(key):
        return {"upper": (0, ""), "lower": (1, ""), "": (3, "")}.get(key, (2, key))
    return [groups[key] for key in sorted(groups, key=order)]


def league_row(req, gid):
    scope = req.group(gid)
    league = req.db.execute("SELECT id, name FROM user_groups WHERE id=?", (gid,)).fetchone()
    return scope, league


@app.route("GET", "/")
def home(req):
    user = req.require_user()
    token = h(req.csrf_token())
    groups = req.db.execute("SELECT g.id, g.name, m.role FROM user_groups g JOIN memberships m ON m.group_id=g.id "
                            "WHERE m.user_id=? ORDER BY g.id", (user["id"],)).fetchall()
    items = "".join(f'<li><a href="/leagues/{g["id"]}">{h(g["name"])}</a> ({h(g["role"])})</li>' for g in groups)
    content = (f"<h2>My leagues</h2><ul>{items or '<li>You are not in a league yet.</li>'}</ul>"
               f'<h2>New league</h2><form method="post" action="/api/groups">'
               f'<input type="hidden" name="csrf_token" value="{token}">'
               f'<label>League name <input name="name" required maxlength="80"></label> '
               f'<button>New league</button></form>')
    return layout(req, "BenchLink", content)


@app.route("GET", "/leagues/<int:gid>")
def league_page(req, gid):
    scope, league = league_row(req, gid)
    games = upcoming_games(scope)
    rows = []
    for game in games:
        g = game_json(scope, game)
        rows.append(f'<tr><td><a href="/leagues/{gid}/games/{g["id"]}">{h(g["starts_at"])}</a></td>'
                    f'<td>{h(g["home_team"]["name"])} ({g["skaters"]["home"]["count"]}/{SKATER_LIMIT})</td>'
                    f'<td>{h(g["away_team"]["name"])} ({g["skaters"]["away"]["count"]}/{SKATER_LIMIT})</td>'
                    f'<td>{h(g["rink"])}</td></tr>')
    table = ("<table><thead><tr><th>Starts</th><th>Home (skaters)</th><th>Away (skaters)</th><th>Rink</th></tr>"
             f"</thead><tbody>{''.join(rows)}</tbody></table>" if rows else "<p>No upcoming games.</p>")
    teams = scope.list("teams", order_by="name_key")
    mine = set(captain_team_ids(scope))
    team_items = "".join(
        f"<h3>{h(heading)}</h3><ul>" + "".join(
            f"<li>{h(t['name'])}{' (you captain this team)' if t['id'] in mine else ''}</li>" for t in group) + "</ul>"
        for heading, group in division_groups(teams))
    my_teams = "".join(f"<li>{h(t['name'])}</li>" for t in teams if t["id"] in mine)
    content = (f"<h2>Upcoming games</h2>{table}"
               f"<h2>My captain teams</h2><ul>{my_teams or '<li>You do not captain a team.</li>'}</ul>"
               f"<h2>Teams</h2>{team_items or '<ul><li>No teams yet.</li></ul>'}"
               f'<p><a href="/leagues/{gid}/roster">Roster</a> | <a href="/leagues/{gid}/me">My profile</a></p>')
    if is_admin(scope):
        options = "".join(f'<option value="{t["id"]}">{h(t["name"])}</option>' for t in teams)
        content += (f'<h2>New game</h2><form data-method="POST" data-path="/api/groups/{gid}/games">'
                    f'<label>Home <select name="home_team_id" data-type="int">{options}</select></label> '
                    f'<label>Away <select name="away_team_id" data-type="int">{options}</select></label> '
                    f'<label>Starts <input type="datetime-local" name="starts_at" required></label> '
                    f'<label>Rink <input name="rink" maxlength="120"></label> <button>Create game</button></form>')
    return layout(req, league["name"], content, gid)


def contact_card(contact, viewer, as_captain, as_sub):
    """The contact card shows the viewer the other people only (revision 3).

    A game captain sees "Your sub" and "Other captain" (every captain of both teams except
    themself); the sub's linked member sees "Your captains" and never their own details.
    """
    def details(phone, email):
        return f"{h(phone or 'no phone on file')}, {h(email or 'no email on file')}"
    others = "".join(f"<li>{h(c['team_name'])} captain {h(c['name'] or c['username'])}: "
                     f"{details(c['phone'], c['email'])}</li>"
                     for c in contact["captains"] if c["username"] != viewer) or "<li>No other captains.</li>"
    parts = []
    if as_captain:
        sub = contact["sub"]
        who = "<p>You are the sub for this request.</p>" if as_sub else \
            f"<p>{h(sub['name'])}: {details(sub['phone'], sub['email'])}</p>"
        parts.append(f"<h5>Your sub</h5>{who}<h5>Other captain</h5><ul>{others}</ul>")
    if as_sub:
        parts.append(f"<h5>Your captains</h5><ul>{others}</ul>")
    return (f'<div class="contact"><h4>Contact details</h4>{"".join(parts)}'
            f"<p>Contact each other directly; BenchLink does not send messages.</p></div>")


@app.route("GET", "/leagues/<int:gid>/games/<int:game_id>")
def game_desk(req, gid, game_id):
    scope, league = league_row(req, gid)
    game = scope.get("games", game_id)
    g = game_json(scope, game)
    sides = captains_game(scope, game)
    rsvps = {r["player_id"]: r["status"] for r in game_rsvps(scope, game)}
    parts = [f"<p>{h(g['starts_at'])} at {h(g['rink'] or 'rink to be announced')}</p>",
             "<p>BenchLink does not send messages. Once a sub is confirmed, the captains and the sub "
             "contact each other themselves.</p>"]
    for side, team in (("home", g["home_team"]), ("away", g["away_team"])):
        c = g["skaters"][side]
        items = []
        for p in scope.db.execute(PLAYER_SELECT + " AND p.team_id=? ORDER BY p.last_name, p.first_name, p.id",
                                  (gid, team["id"])).fetchall():
            status = rsvps.get(p["id"], "no reply")
            buttons = ""
            if may_set_rsvp(scope, p):
                path = f"/api/groups/{gid}/games/{game_id}/rsvps/{p['id']}"
                buttons = " " + api_button("In", "PUT", path, {"status": "in"}) + api_button(
                    "Out", "PUT", path, {"status": "out"})
            items.append(f"<li>{h(full_name(p))}{' (G)' if p['is_goalie'] else ''}: {h(status)}{buttons}</li>")
        parts.append(f"<h2>{h(side.title())}: {h(team['name'])}</h2><p>Skaters {c['count']}/{c['limit']} "
                     f"({c['rsvp_in']} in, {c['rsvp_out']} out, {c['confirmed_subs']} confirmed subs, "
                     f"{c['goalies_in']} goalies in)</p><ul>{''.join(items) or '<li>No players.</li>'}</ul>")
    subs = []
    for p in available_subs(scope, game):
        buttons = "".join(" " + api_button(f"Request for {team['name']}", "POST",
                                           f"/api/groups/{gid}/games/{game_id}/sub-requests",
                                           {"team_id": team["id"], "player_id": p["id"]})
                          for side, team in (("home", g["home_team"]), ("away", g["away_team"])) if side in sides)
        subs.append(f"<li>{h(full_name(p))} ({h(p['team_name'])}{', #' + h(p['jersey']) if p['jersey'] else ''}"
                    f"{', goalie' if p['is_goalie'] else ''}){buttons}</li>")
    parts.append(f"<h2>Available subs</h2><ul>{''.join(subs) or '<li>No available subs.</li>'}</ul>")
    requests = []
    for sr in game_requests(scope, game_id):
        data = sub_request_json(scope, sr, with_contact=True)
        buttons = ""
        if sides and sr["status"] == "pending":
            buttons = " " + api_button("Approve", "POST", f"/api/groups/{gid}/sub-requests/{sr['id']}/approve") + \
                api_button("Decline", "POST", f"/api/groups/{gid}/sub-requests/{sr['id']}/decline")
        approvals = ", ".join(f"{side} {'approved' if data['approvals'][side] else 'waiting'}"
                              for side in ("home", "away"))
        card = ""
        if "contact" in data:
            sub_user = load_player(scope, sr["player_id"])["user_id"]
            card = contact_card(data["contact"], req.user["username"], bool(sides),
                                sub_user is not None and sub_user == scope.user_id)
        requests.append(f"<li>{h(data['player']['name'])} for {h(data['team_name'])}: {h(data['status'])} "
                        f"({h(approvals)}){buttons}{card}</li>")
    parts.append(f"<h2>Sub requests</h2><ul>{''.join(requests) or '<li>No sub requests.</li>'}</ul>")
    title = f"{g['home_team']['name']} vs {g['away_team']['name']}"
    return layout(req, title, "".join(parts), gid)


def roster_team_section(scope, gid, team, admin):
    t = team_json(scope, team)
    caps = ", ".join(h(c["username"]) + (" " + api_button(
        "Remove", "DELETE", f"/api/groups/{gid}/teams/{team['id']}/captains/{c['user_id']}") if admin else "")
        for c in t["captains"]) or "none"
    rows = []
    for p in scope.db.execute(PLAYER_SELECT + " AND p.team_id=? ORDER BY p.last_name, p.first_name, p.id",
                              (gid, team["id"])).fetchall():
        link = ""
        if admin:
            if p["user_id"] is None:
                link = (f'<form data-method="POST" data-path="/api/groups/{gid}/players/{p["id"]}/link">'
                        f'<input name="username" placeholder="member username" required> '
                        f'<button>Link account</button></form>')
            else:
                link = api_button("Unlink account", "DELETE", f"/api/groups/{gid}/players/{p['id']}/link")
        marker = " <em>Captain (on link)</em>" if p["captain_on_link"] and p["user_id"] is None else ""
        rows.append(f"<tr><td>{h(p['jersey'])}</td><td>{h(full_name(p))}{marker}</td>"
                    f"<td>{'goalie' if p['is_goalie'] else 'skater'}</td>"
                    f"<td>{'yes' if p['phone'] else 'no'}</td><td>{'yes' if p['available_as_sub'] else 'no'}</td>"
                    f"<td>{'linked' if p['user_id'] is not None else 'not linked'}</td><td>{link}</td></tr>")
    forms = ""
    if admin:
        forms = (f'<form data-method="POST" data-path="/api/groups/{gid}/teams/{team["id"]}/captains">'
                 f'<input name="username" placeholder="member username" required> '
                 f'<button>Assign captain</button></form>'
                 f'<form data-method="PATCH" data-path="/api/groups/{gid}/teams/{team["id"]}">'
                 f'<label>Division <input name="division" data-keep-empty="1" maxlength="40" '
                 f'value="{h(team["division"])}" placeholder="Upper or Lower"></label> '
                 f'<button>Set division</button></form>')
    return (f"<h3>{h(team['name'])}</h3><p>Captains: {caps}</p>{forms}"
            "<table><thead><tr><th>#</th><th>Name</th><th>Position</th><th>Phone on file</th>"
            "<th>Available to sub</th><th>Account</th><th></th></tr></thead>"
            f"<tbody>{''.join(rows)}</tbody></table>")


@app.route("GET", "/leagues/<int:gid>/roster")
def roster_page(req, gid):
    scope, league = league_row(req, gid)
    admin = is_admin(scope)
    parts = []
    teams = scope.list("teams", order_by="name_key")
    for heading, group in division_groups(teams):
        parts.append(f"<h2>{h(heading)}</h2>")
        parts.extend(roster_team_section(scope, gid, team, admin) for team in group)
    if not teams:
        parts.append("<p>No teams yet.</p>")
    if admin:
        imports = scope.db.execute("SELECT id, status, filename, created_at FROM roster_imports WHERE group_id=? "
                                   "ORDER BY created_at DESC, id DESC LIMIT 10", (gid,)).fetchall()
        links = "".join(f'<li><a href="/leagues/{gid}/imports/{r["id"]}">Import {r["id"]}'
                        f'{" (" + h(r["filename"]) + ")" if r["filename"] else ""}</a>: {h(r["status"])}</li>'
                        for r in imports)
        parts.append(
            f'<h2>Import roster</h2><form data-kind="import" data-method="POST" data-path="/api/groups/{gid}/imports" '
            f'data-next="/leagues/{gid}/imports/">'
            f'<p><label for="roster-file">Upload a SportsEngine roster export (CSV)</label> '
            f'<input id="roster-file" type="file" name="file" accept=".csv,text/csv"> '
            f'<small>In Excel, use File → Save As → CSV UTF-8, then upload.</small></p>'
            f'<p><label>Or paste the CSV text<br><textarea name="csv" rows="6" cols="80"></textarea></label></p>'
            f'<p><label>Header mapping (optional JSON, e.g. {h(json.dumps({"Skater": "full_name", "Notes": None}))})'
            f'<br><textarea name="mapping" rows="3" cols="80"></textarea></label></p>'
            f"<p>The file is only previewed until you commit it. BenchLink has no live SportsEngine connection.</p>"
            f"<button>Preview import</button></form><h3>Recent imports</h3><ul>{links or '<li>None yet.</li>'}</ul>"
            f'<h2>New team</h2><form data-method="POST" data-path="/api/groups/{gid}/teams">'
            f'<input name="name" required maxlength="80"> <button>Create team</button></form>')
    return layout(req, f"{league['name']} roster", "".join(parts), gid)


@app.route("GET", "/leagues/<int:gid>/imports/<int:iid>")
def import_page(req, gid, iid):
    scope = req.group(gid, roles=ADMIN_ROLES)
    record = scope.get("roster_imports", iid)
    if record["status"] == "committed":
        summary = json.loads(record["summary_json"])
        content = ("<p>This import has been committed.</p><ul>" +
                   "".join(f"<li>{h(k)}: {h(str(v))}</li>" for k, v in summary.items()) + "</ul>")
        return layout(req, f"Import {iid}", content, gid)
    preview = import_plan(scope, record).preview(record)
    summary = " ".join(f"{h(k)}: {h(str(v))}" for k, v in preview["summary"].items())
    rows = "".join(f"<tr><td>{r['row']}</td><td>{h(r['action'])}</td><td>{h(r['name'])}</td><td>{h(r['team'])}</td>"
                   f"<td>{h(r['division'])}</td><td>{'yes' if r['captain'] else ''}</td>"
                   f"<td>{h(r['phone_status'])}</td><td>{h('; '.join(r['warnings']))}</td>"
                   f"<td>{h('; '.join(r['errors']))}</td></tr>" for r in preview["rows"])
    columns = "".join(f"<li>{h(c['header'])}: {h(c['field'] or 'ignored')}</li>" for c in preview["columns"])
    content = (f"<p>Preview only: nothing is saved until you commit. {summary}</p><h2>Columns</h2><ul>{columns}</ul>"
               "<table><thead><tr><th>Row</th><th>Action</th><th>Name</th><th>Team</th><th>Division</th>"
               "<th>Captain</th><th>Phone</th>"
               f"<th>Warnings</th><th>Errors</th></tr></thead><tbody>{rows}</tbody></table>"
               + api_button("Commit import", "POST", f"/api/groups/{gid}/imports/{iid}/commit"))
    return layout(req, f"Import {iid} preview", content, gid)


@app.route("GET", "/leagues/<int:gid>/me")
def me_page(req, gid):
    scope, league = league_row(req, gid)
    player = my_player(scope)
    role = "league admin" if is_admin(scope) else "member"
    mine = [t["name"] for t in scope.list("teams", order_by="name_key") if t["id"] in set(captain_team_ids(scope))]
    parts = [f"<p>Role: {h(role)}. Captain of: {h(', '.join(mine) or 'no team')}.</p>"]
    if player is None:
        parts.append("<p>Your account is not linked to a player yet. Ask a league admin to link it. "
                     "We do not text this number yet.</p>")
    else:
        path = f"/api/groups/{gid}/me/player"
        switch = api_button("Stop being available to sub" if player["available_as_sub"] else "Available to sub",
                            "PATCH", path, {"available_as_sub": not player["available_as_sub"]})
        assignments = scope.db.execute("SELECT id, game_id, status FROM sub_requests WHERE group_id=? AND player_id=? "
                                       "ORDER BY id", (gid, player["id"])).fetchall()
        items = "".join(f'<li><a href="/leagues/{gid}/games/{a["game_id"]}">Game {a["game_id"]}</a>: '
                        f'{h(a["status"])}</li>' for a in assignments)
        parts.append(
            f"<h2>{h(full_name(player))}</h2><p>Team: {h(player['team_name'])}. Jersey: {h(player['jersey'] or '-')}."
            f"</p><p>Email: {h(player['email'] or 'none on file')}</p>"
            f'<form data-method="PATCH" data-path="{h(path)}"><label>My phone '
            f'<input name="phone" data-keep-empty="1" value="{h(player["phone"])}" placeholder="(403) 555-0101"></label> '
            f"<button>Save phone</button> <small>We do not text this number yet.</small></form>"
            f"<p>Available to sub: {'yes' if player['available_as_sub'] else 'no'} {switch}</p>"
            f"<h2>My sub assignments</h2><ul>{items or '<li>None.</li>'}</ul>")
    return layout(req, f"My profile in {league['name']}", "".join(parts), gid)


if __name__ == "__main__":
    appkit.serve(app)
