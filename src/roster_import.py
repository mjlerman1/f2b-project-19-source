"""SportsEngine roster CSV import for BenchLink: parsing, header mapping and row planning.

Everything here is scoped to one league: every SQL statement filters on group_id.
Preview output never contains phone numbers or email addresses, only phone_status.
Revision 3 adds the division and captain columns (a roster CSV saved from Excel) and
the captain-on-link rule: a marked player who has a linked account captains their team.
"""
import csv
import io
import json
import re
import time

MAX_CSV_BYTES = 512 * 1024
MAX_DATA_ROWS = 2000
MAX_TEAM_NAME = 80
MAX_DIVISION = 40
FIELDS = ("external_id", "first_name", "last_name", "full_name", "team", "jersey", "position", "email", "phone",
          "division", "captain")
ALIASES = {
    "external_id": ("memberid", "sportsengineid", "profileid", "personid", "participantid", "playerid", "id"),
    "first_name": ("firstname", "participantfirstname", "playerfirstname", "givenname", "athlete1firstname"),
    "last_name": ("lastname", "participantlastname", "playerlastname", "surname", "familyname", "athlete1lastname"),
    "full_name": ("name", "playername", "fullname", "participantname"),
    "team": ("team", "teamname", "roster", "rostername"),
    "jersey": ("jersey", "jerseynumber", "jersey#", "jerseyno", "number", "#", "uniformnumber"),
    "position": ("position", "positions", "pos", "primaryposition"),
    "email": ("email", "emailaddress", "participantemail", "playeremail", "primaryemail", "email1"),
    "phone": ("phone", "phonenumber", "mobile", "mobilephone", "cellphone", "cell", "participantphone",
              "playerphone", "primaryphone", "phone1", "homephone"),
    "division": ("division", "league", "tier", "level"),
    "captain": ("captain", "iscaptain", "teamcaptain"),
}
ALIAS_TO_FIELD = {alias: field for field, aliases in ALIASES.items() for alias in aliases}
GOALIE_POSITIONS = {"g", "goalie", "goaltender", "gk"}
CAPTAIN_YES = {"y", "yes", "true", "1", "x", "c", "captain"}
BOM = "\ufeff"


class RosterError(ValueError):
    """The upload cannot be previewed at all (HTTP 400)."""


def normalize_phone(value):
    """NANP number to +1XXXXXXXXXX, or None when invalid."""
    if not isinstance(value, str):
        return None
    digits = re.sub(r"[^0-9]", "", value)
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    if len(digits) != 10 or digits[0] in "01":
        return None
    return "+1" + digits


def name_key(name):
    return " ".join(name.split()).lower()


def collapse(text):
    """Trim and collapse inner whitespace (divisions)."""
    return " ".join(text.split())


def captain_from(cell):
    """(is_captain, recognised) for a captain cell; blank is a recognised "no"."""
    value = cell.strip().lower()
    if not value:
        return False, True
    return value in CAPTAIN_YES, value in CAPTAIN_YES


def normalize_header(text):
    return re.sub(r"[^a-z0-9#]", "", text.replace(BOM, "").lower())


def parse_csv(text):
    """Return (headers, data_rows); data_rows are (row_number, cells) with blank lines skipped."""
    if len(text.encode("utf-8", "surrogatepass")) > MAX_CSV_BYTES:
        raise RosterError("The CSV file is larger than 512 KiB")
    if text.startswith(BOM):
        text = text[1:]
    try:
        records = list(csv.reader(io.StringIO(text, newline=""), delimiter=",", quotechar='"'))
    except csv.Error:
        raise RosterError("The CSV file could not be read") from None
    headers, header_index, data = None, None, []
    for index, record in enumerate(records):
        cells = [cell.strip() for cell in record]
        if not any(cells):
            continue
        if headers is None:
            headers, header_index = [c.replace(BOM, "").strip() for c in cells], index
            continue
        data.append((index - header_index + 1, cells))
        if len(data) > MAX_DATA_ROWS:
            raise RosterError("The CSV file has more than 2,000 data rows")
    if headers is None:
        raise RosterError("The CSV file has no header row")
    return headers, data


def validate_mapping(mapping):
    if mapping is None:
        return {}
    if not isinstance(mapping, dict):
        raise RosterError("mapping must be an object from header to field name or null")
    for key, value in mapping.items():
        if value is not None and value not in FIELDS:
            raise RosterError("mapping values must be one of: " + ", ".join(FIELDS) + " or null")
    return {key.replace(BOM, "").strip(): value for key, value in mapping.items()}


def resolve_columns(headers, mapping):
    known = set(headers)
    for key in mapping:
        if key not in known:
            raise RosterError("mapping names a header that is not in the file")
    columns = []
    for header in headers:
        field = mapping[header] if header in mapping else ALIAS_TO_FIELD.get(normalize_header(header))
        columns.append((header, field))
    fields = {field for _, field in columns}
    if "team" not in fields:
        raise RosterError("No column maps to team; add a mapping for the team column")
    if "first_name" not in fields and "full_name" not in fields:
        raise RosterError("No column maps to first_name or full_name; add a mapping for the name column")
    return columns


def _values(columns, cells):
    values = {}
    for position, (_, field) in enumerate(columns):
        if field is None or values.get(field):
            continue
        cell = cells[position] if position < len(cells) else ""
        if cell:
            values[field] = cell
    return values


def split_name(values):
    first, last = values.get("first_name", ""), values.get("last_name", "")
    if first:
        return first, last
    full = values.get("full_name", "")
    if not full:
        return "", ""
    if "," in full:
        last, _, first = full.partition(",")
        return first.strip(), last.strip()
    words = full.split()
    if len(words) == 1:
        return words[0], ""
    return " ".join(words[:-1]), words[-1]


def goalie_from(position):
    if not position:
        return None
    return re.sub(r"[^a-z0-9]", "", position.lower()) in GOALIE_POSITIONS


class Plan:
    def __init__(self, columns, rows, teams_by_key, players):
        self.columns, self.rows, self.teams_by_key, self.players = columns, rows, teams_by_key, players

    def summary(self):
        counts = {"total": len(self.rows), "create": 0, "update": 0, "unchanged": 0, "duplicate": 0, "error": 0}
        for row in self.rows:
            counts[row["action"]] += 1
        return counts

    def preview(self, record):
        return {"id": record["id"], "status": "preview", "filename": record["filename"],
                "columns": [{"header": header, "field": field} for header, field in self.columns],
                "ignored_columns": [header for header, field in self.columns if field is None],
                "summary": self.summary(),
                "rows": [{k: row[k] for k in ("row", "action", "name", "team", "jersey", "is_goalie",
                                              "phone_status", "warnings", "errors", "division", "captain")}
                         for row in self.rows]}


def build_plan(db, group_id, text, mapping):
    """Plan every row against the league's current teams and players; writes nothing."""
    headers, data = parse_csv(text)
    columns = resolve_columns(headers, validate_mapping(mapping))
    teams_by_key = {r["name_key"]: r for r in db.execute(
        "SELECT * FROM teams WHERE group_id=? ORDER BY id", (group_id,))}
    players = [dict(r) for r in db.execute("SELECT * FROM players WHERE group_id=? ORDER BY id", (group_id,))]
    by_ext, by_email, by_name = {}, {}, {}
    for p in players:
        if p["external_id"]:
            by_ext.setdefault(p["external_id"].lower(), p)
        if p["email"]:
            by_email.setdefault(p["email"].lower(), p)
        by_name.setdefault((p["first_name"].lower(), p["last_name"].lower(), p["team_id"]), p)
    team_divisions = {}
    for _, cells in data:
        values = _values(columns, cells)
        team_text, division = values.get("team", ""), collapse(values.get("division", ""))
        if team_text and division:
            team_divisions.setdefault(name_key(team_text), set()).add(division.lower())
    split_teams = {key for key, divisions in team_divisions.items() if len(divisions) > 1}
    seen, rows = {}, []
    for number, cells in data:
        values = _values(columns, cells)
        first, last = split_name(values)
        team_text = values.get("team", "")
        errors, warnings = [], []
        if not first:
            errors.append("missing name")
        if not team_text:
            errors.append("missing team")
        elif len(team_text) > MAX_TEAM_NAME:
            errors.append("team name is longer than 80 characters")
        division = collapse(values.get("division", ""))
        if len(division) > MAX_DIVISION:
            errors.append("division too long")
        if team_text and name_key(team_text) in split_teams:
            errors.append("team in two divisions")
        captain, recognised = captain_from(values.get("captain", ""))
        if not recognised:
            warnings.append("captain cell not recognised")
        raw_phone = values.get("phone", "")
        phone = normalize_phone(raw_phone) if raw_phone else None
        if not raw_phone:
            phone_status = "missing"
        elif phone:
            phone_status = "present"
        else:
            phone_status = "invalid"
            warnings.append("invalid phone number; the player is imported without a phone")
        email = values.get("email", "").lower()
        external_id = values.get("external_id", "")
        jersey = values.get("jersey", "")
        goalie = goalie_from(values.get("position", ""))
        tkey = name_key(team_text)
        team_row = teams_by_key.get(tkey)
        row = {"row": number, "action": "error", "name": (first + " " + last).strip(), "team": team_row["name"] if team_row else team_text,
               "jersey": jersey, "is_goalie": bool(goalie), "phone_status": phone_status,
               "warnings": warnings, "errors": errors, "division": division, "captain": captain,
               "player": None, "changes": {},
               "team_key": tkey, "team_text": team_text,
               "values": {"first_name": first, "last_name": last, "jersey": jersey, "goalie": goalie,
                          "email": email, "external_id": external_id, "phone": phone}}
        rows.append(row)
        if errors:
            continue
        if external_id:
            key = "ext:" + external_id.lower()
        elif email:
            key = "email:" + email
        else:
            key = f"name:{first} {last}|{tkey}".lower()
        if key in seen:
            row["action"] = "duplicate"
            warnings.append(f"duplicate of row {seen[key]}; skipped")
            continue
        seen[key] = number
        match = None
        if external_id:
            match = by_ext.get(external_id.lower())
        if match is None and email:
            match = by_email.get(email)
        if match is None and team_row is not None:
            match = by_name.get((first.lower(), last.lower(), team_row["id"]))
        if match is None:
            row["action"] = "create"
            continue
        row["player"] = match
        changes = {}
        if match["first_name"] != first:
            changes["first_name"] = first
        if match["last_name"] != last:
            changes["last_name"] = last
        if team_row is None or team_row["id"] != match["team_id"]:
            changes["team_id"] = team_row["id"] if team_row else None
        if jersey and match["jersey"] != jersey:
            changes["jersey"] = jersey
        if goalie is not None and bool(match["is_goalie"]) != goalie:
            changes["is_goalie"] = int(goalie)
        if email and match["email"] != email:
            changes["email"] = email
        if external_id and not match["external_id"]:
            changes["external_id"] = external_id
        if phone and not match["phone"]:
            changes["phone"] = phone
            changes["phone_source"] = "import"
        if captain and not match["captain_on_link"]:
            changes["captain_on_link"] = 1
        row["changes"] = changes
        row["action"] = "update" if changes else "unchanged"
        if not jersey:
            row["jersey"] = match["jersey"]
        if goalie is None:
            row["is_goalie"] = bool(match["is_goalie"])
    return Plan(columns, rows, teams_by_key, players)


def commit_plan(db, group_id, plan):
    """Apply a plan inside the caller's transaction; returns (teams, rows).

    The first committed row of a team with a non-empty division sets that team's division.
    A committed captain "yes" row whose player has a linked account makes that account a
    captain of the player's team (INSERT OR IGNORE: never fails, never duplicates).
    """
    now = int(time.time())
    team_ids, teams_out, rows_out, division_set = {}, [], [], set()
    for key, team in plan.teams_by_key.items():
        team_ids[key] = team["id"]
    for row in plan.rows:
        if row["action"] in ("duplicate", "error"):
            rows_out.append({"row": row["row"], "action": row["action"], "player_id": None})
            continue
        key = row["team_key"]
        if key not in team_ids:
            team_ids[key] = db.execute(
                "INSERT INTO teams (group_id, name, name_key, created_at) VALUES (?, ?, ?, ?)",
                (group_id, row["team_text"], key, now)).lastrowid
        team_id = team_ids[key]
        if all(t["id"] != team_id for t in teams_out):
            name = db.execute("SELECT name FROM teams WHERE id=? AND group_id=?", (team_id, group_id)).fetchone()[0]
            teams_out.append({"id": team_id, "name": name})
        if row["division"] and key not in division_set:
            division_set.add(key)
            db.execute("UPDATE teams SET division=? WHERE id=? AND group_id=? AND division<>?",
                       (row["division"], team_id, group_id, row["division"]))
        v = row["values"]
        if row["action"] == "create":
            player_id = db.execute(
                "INSERT INTO players (group_id, team_id, first_name, last_name, jersey, is_goalie, external_id, "
                "email, phone, phone_source, available_as_sub, user_id, captain_on_link, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, NULL, ?, ?)",
                (group_id, team_id, v["first_name"], v["last_name"], v["jersey"], int(bool(v["goalie"])),
                 v["external_id"], v["email"], v["phone"] or "", "import" if v["phone"] else "",
                 int(row["captain"]), now)).lastrowid
        else:
            player_id = row["player"]["id"]
            changes = dict(row["changes"])
            if "team_id" in changes:
                changes["team_id"] = team_id
            if "phone" in changes:
                # Never overwrite a phone stored since the plan was made (e.g. by the player).
                current = db.execute("SELECT phone FROM players WHERE id=? AND group_id=?",
                                     (player_id, group_id)).fetchone()[0]
                if current:
                    changes.pop("phone")
                    changes.pop("phone_source")
            if changes:
                sets = ", ".join(f'"{k}"=?' for k in changes)
                db.execute(f"UPDATE players SET {sets} WHERE id=? AND group_id=?",
                           (*changes.values(), player_id, group_id))
        if row["captain"]:
            grant_captain_on_link(db, group_id, player_id, now)
        rows_out.append({"row": row["row"], "action": row["action"], "player_id": player_id})
    return teams_out, rows_out


def grant_captain_on_link(db, group_id, player_id, now=None):
    """If the player is marked captain_on_link and linked, captain their current team."""
    player = db.execute("SELECT team_id, user_id, captain_on_link FROM players WHERE id=? AND group_id=?",
                        (player_id, group_id)).fetchone()
    if player is None or not player["captain_on_link"] or player["user_id"] is None:
        return
    db.execute("INSERT OR IGNORE INTO team_captains (group_id, team_id, user_id, created_at) VALUES (?, ?, ?, ?)",
               (group_id, player["team_id"], player["user_id"], int(time.time()) if now is None else now))


def mapping_json(mapping):
    return json.dumps(validate_mapping(mapping), separators=(",", ":"))
