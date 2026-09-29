"""Builder-side tests for BenchLink revision 3 (run: python -B -m unittest discover tests).

These are the candidate's own tests. Independent acceptance comes only from the
operator's harness; these tests never count as acceptance evidence. Each test
class names the acceptance id it exercises.
"""
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "src"))
import appkit  # noqa: E402
import main  # noqa: E402

SAMPLE = (HERE / "sample_sportsengine_roster.txt").read_text("utf-8")
LEAGUE_SAMPLE = (HERE / "sample_league_roster_upload.txt").read_text("utf-8")
EXCEL_HEADER = "\ufeffathlete_1_first_name,athlete_1_last_name,Cell Phone,Team,League,Captain,Notes\r\n"


def excel_roster(*rows):
    """A roster CSV as Excel saves it (BOM, CRLF) with division and captain columns."""
    return EXCEL_HEADER + "".join(",".join(r) + "\r\n" for r in rows)
HEADER = "Member ID,First Name,Last Name,Team Name,Jersey #,Position,Email Address,Cell Phone,Birth Date\n"


def roster(*rows):
    return HEADER + "".join(",".join(r) + "\n" for r in rows)


def skater(ext, first, last, team, jersey="10", pos="F", email="", phone=""):
    return (ext, first, last, team, jersey, pos, email, phone, "1990-01-01")


class BenchLinkCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data_dir = self.tmp.name
        main.app.open(self.data_dir)
        self.users = {}
        self.owner = self.user("owner")
        self.gid = self.owner.post("/api/groups", {"name": "Wednesday League"}).json()["group"]["id"]

    def tearDown(self):
        self.tmp.cleanup()

    # -- helpers --
    def user(self, name, gid=None, role="member"):
        client = appkit.Client(main.app)
        self.assertEqual(client.signup(name).status, 201)
        self.users[name] = client
        if gid is not None:
            self.add(name, gid, role)
        return client

    def add(self, name, gid=None, role="member"):
        r = self.owner.post(f"/api/groups/{gid or self.gid}/members", {"username": name, "role": role})
        self.assertEqual(r.status, 201, r.text)

    def member(self, name, role="member"):
        return self.user(name, self.gid, role)

    def api(self, path):
        return f"/api/groups/{self.gid}{path}"

    def upload(self, csv_text, client=None, mapping=None, status=201):
        body = {"csv": csv_text, "filename": "roster.csv"}
        if mapping is not None:
            body["mapping"] = mapping
        r = (client or self.owner).post(self.api("/imports"), body)
        self.assertEqual(r.status, status, r.text)
        return r.json()

    def import_roster(self, csv_text, mapping=None):
        preview = self.upload(csv_text, mapping=mapping)["import"]
        r = self.owner.post(self.api(f"/imports/{preview['id']}/commit"))
        self.assertEqual(r.status, 200, r.text)
        return r.json()

    def players(self, client=None):
        return {p["name"]: p for p in (client or self.owner).get(self.api("/players")).json()["players"]}

    def teams(self):
        return {t["name"]: t for t in self.owner.get(self.api("/teams")).json()["teams"]}

    def captain(self, team_name, username):
        r = self.owner.post(self.api(f"/teams/{self.teams()[team_name]['id']}/captains"), {"username": username})
        self.assertEqual(r.status, 201, r.text)

    def link(self, player_name, username):
        r = self.owner.post(self.api(f"/players/{self.players()[player_name]['id']}/link"), {"username": username})
        self.assertEqual(r.status, 200, r.text)

    def patch_me(self, client, body):
        return client.request("PATCH", self.api("/me/player"), json_body=body)

    def game(self, home, away, starts_at="2099-01-01T20:00", rink="Rink 1"):
        teams = self.teams()
        r = self.owner.post(self.api("/games"), {"home_team_id": teams[home]["id"], "away_team_id": teams[away]["id"],
                                                 "starts_at": starts_at, "rink": rink})
        self.assertEqual(r.status, 201, r.text)
        return r.json()["game"]

    def rsvp(self, client, game_id, player_id, status):
        return client.request("PUT", self.api(f"/games/{game_id}/rsvps/{player_id}"), json_body={"status": status})

    def request_sub(self, client, game_id, team_id, player_id):
        return client.post(self.api(f"/games/{game_id}/sub-requests"), {"team_id": team_id, "player_id": player_id})

    def assert_no_contact(self, text, *secrets):
        for secret in secrets:
            self.assertNotIn(secret, text)


def three_team_setup(case):
    """Teams A, B, C with captains cap_a/cap_b/cap_c and linked, opted-in players."""
    case.import_roster(roster(
        skater("A1", "Alex", "Ames", "Aces", phone="403-555-0101", email="alex@example.com"),
        skater("A2", "Ari", "Abbott", "Aces", phone="403-555-0102"),
        skater("B1", "Bo", "Banks", "Bears", phone="403-555-0111"),
        skater("B2", "Bee", "Burns", "Bears", phone="403-555-0112"),
        skater("C1", "Cy", "Cole", "Comets", phone="403-555-0121", email="cy@example.com"),
        skater("C2", "Cam", "Cruz", "Comets", phone="403-555-0122"),
    ))
    for name in ("cap_a", "cap_b", "cap_c", "p_a", "p_b", "p_c"):
        case.member(name)
    case.captain("Aces", "cap_a")
    case.captain("Bears", "cap_b")
    case.captain("Comets", "cap_c")
    case.link("Ari Abbott", "cap_a")
    case.link("Alex Ames", "p_a")
    case.link("Bo Banks", "p_b")
    case.link("Cy Cole", "p_c")
    for name in ("p_a", "p_b", "p_c"):
        case.assertEqual(case.patch_me(case.users[name], {"available_as_sub": True}).status, 200)


class RosterCsvImportTest(BenchLinkCase):
    """roster-csv-import"""

    CSV = roster(
        skater("SE-1", "Avery", "Stone", "Harbour Hawks", "12", "Forward", "Avery@Example.com", "(403) 555-0101"),
        skater("SE-2", "Blake", "Moreau", "Harbour Hawks", "4", "Defense", "blake@example.com", ""),
        skater("SE-3", "Casey", "Lind", "Harbour Hawks", "31", "Goalie", "", "555-0103"),
        skater("SE-4", "Drew", "Okafor", "North Stars", "9", "F", "drew@example.com", "403.555.0104"),
        skater("SE-1", "Avery", "Stone", "Harbour Hawks", "12", "Forward", "Avery@Example.com", "(403) 555-0101"),
        skater("SE-5", "", "", "North Stars", "", "", "", ""),
    )

    def test_preview_commit_reimport_and_update(self):
        player = self.member("plain")
        self.assertEqual(self.upload(self.CSV, client=player, status=403)["error"] != "", True)
        preview = self.upload(self.CSV)["import"]
        self.assertEqual(preview["status"], "preview")
        self.assertEqual(preview["summary"], {"total": 6, "create": 4, "update": 0, "unchanged": 0,
                                              "duplicate": 1, "error": 1})
        self.assertIn("Birth Date", preview["ignored_columns"])
        self.assertEqual({r["phone_status"] for r in preview["rows"]}, {"present", "missing", "invalid"})
        text = json.dumps(preview)
        self.assert_no_contact(text, "4035550101", "555-0101", "@example.com", "avery@")
        self.assertEqual(self.owner.get(self.api("/players")).json()["players"], [])
        self.assertEqual(self.owner.get(self.api("/teams")).json()["teams"], [])
        first = self.owner.post(self.api(f"/imports/{preview['id']}/commit"))
        self.assertEqual(first.status, 200)
        self.assert_no_contact(first.text, "4035550101", "@example.com")
        self.assertEqual([r["action"] for r in first.json()["rows"]],
                         ["create", "create", "create", "create", "duplicate", "error"])
        self.assertIsNone(first.json()["rows"][4]["player_id"])
        self.assertEqual(self.owner.post(self.api(f"/imports/{preview['id']}/commit")).status, 409)
        players = self.players(self.member("viewer"))
        self.assertEqual(sorted(players), ["Avery Stone", "Blake Moreau", "Casey Lind", "Drew Okafor"])
        self.assertTrue(players["Casey Lind"]["is_goalie"])
        self.assertFalse(players["Casey Lind"]["has_phone"])
        self.assertFalse(players["Blake Moreau"]["has_phone"])
        self.assertTrue(players["Avery Stone"]["has_phone"])
        again = self.upload(self.CSV)["import"]
        self.assertEqual((again["summary"]["create"], again["summary"]["unchanged"]), (0, 4))
        moved = roster(skater("SE-2", "Blake", "Moreau", "North Stars", "4", "Defense", "blake@example.com",
                              "403 555 0199"))
        preview2 = self.upload(moved)["import"]
        self.assertEqual(preview2["summary"]["update"], 1)
        self.owner.post(self.api(f"/imports/{preview2['id']}/commit"))
        blake = self.players()["Blake Moreau"]
        self.assertEqual(blake["team_name"], "North Stars")
        self.assertTrue(blake["has_phone"])
        listing = self.owner.get(self.api("/imports"))
        self.assertEqual([i["status"] for i in listing.json()["imports"]][-1], "committed")
        self.assert_no_contact(listing.text, "403555", "@example.com")
        with sqlite3.connect(Path(self.data_dir) / "app.db") as conn:
            self.assertEqual(conn.execute("SELECT csv_text FROM roster_imports WHERE id=?",
                                          (preview["id"],)).fetchone()[0], "")
            self.assertEqual(conn.execute("SELECT email FROM players WHERE external_id='SE-1'").fetchone()[0],
                             "avery@example.com")

    def test_sample_sportsengine_export(self):
        preview = self.upload(SAMPLE)["import"]
        self.assertEqual(preview["summary"], {"total": 20, "create": 18, "update": 0, "unchanged": 0,
                                              "duplicate": 1, "error": 1})
        self.assertEqual(preview["rows"][0]["row"], 2)
        self.assertEqual(preview["ignored_columns"], ["Roster Status", "Birth Date"])
        self.assert_no_contact(json.dumps(preview), "555-0101", "4035550101", "example.com")
        result = self.owner.post(self.api(f"/imports/{preview['id']}/commit")).json()
        self.assertEqual([t["name"] for t in result["teams"]],
                         ["Harbour Hawks", "North Stars", "Valley Wolves", "Ridge Rovers"])
        again = self.upload(SAMPLE)["import"]["summary"]
        self.assertEqual((again["create"], again["unchanged"], again["duplicate"]), (0, 18, 1))


class HeaderMappingTest(BenchLinkCase):
    """import-header-mapping"""

    def test_mapping_and_tolerant_parsing(self):
        csv1 = 'Skater,Club,Txt Number,Sweater\n"Stone, Jordan",Harbour Hawks,403-555-0177,77\nSam Lee,Harbour Hawks,,5\n'
        self.upload(csv1, status=400)
        self.upload(csv1, mapping={"Skater": "fullname", "Club": "team"}, status=400)
        self.upload(csv1, mapping={"Skater": "full_name", "Club": "team", "Nope": "phone"}, status=400)
        mapping = {"Skater": "full_name", "Club": "team", "Txt Number": "phone", "Sweater": "jersey"}
        preview = self.upload(csv1, mapping=mapping)["import"]
        self.assertEqual((preview["summary"]["create"], preview["summary"]["error"]), (2, 0))
        self.assertEqual(preview["rows"][0]["name"], "Jordan Stone")
        self.assertNotIn("555-0177", json.dumps(preview))
        self.owner.post(self.api(f"/imports/{preview['id']}/commit"))
        jordan = self.players()["Jordan Stone"]
        self.assertEqual((jordan["first_name"], jordan["jersey"], jordan["has_phone"]), ("Jordan", "77", True))

        csv2 = ("\ufeffPlayer First Name,Player Last Name,Roster Name,E-mail,Phone 1,Mobile Phone,Notes\r\n"
                'Riley,Park,North Stars,riley@example.com,,(403) 555-0188,"Likes early games, weekdays"\r\n'
                "\r\n"
                "Morgan,Fox,North Stars\r\n")
        preview2 = self.upload(csv2)["import"]
        self.assertEqual((preview2["summary"]["create"], preview2["summary"]["error"]), (2, 0))
        self.assertEqual(preview2["ignored_columns"], ["Notes"])
        self.assertEqual(preview2["rows"][0]["row"], 2)
        self.assertEqual(preview2["rows"][1]["row"], 4)
        self.assert_no_contact(json.dumps(preview2), "riley@", "555-0188", "4035550188")
        result = self.owner.post(self.api(f"/imports/{preview2['id']}/commit")).json()
        self.assertEqual([t["name"] for t in result["teams"]], ["North Stars"])
        players = self.players()
        self.assertIn("Riley Park", players)
        self.assertIn("Morgan Fox", players)
        self.assertTrue(players["Riley Park"]["has_phone"])

        csv3 = "Name,Team,Phone\nPat Doe,Aces,403-555-0166\n"
        preview3 = self.upload(csv3, mapping={"Phone": None})["import"]
        self.assertIn("Phone", preview3["ignored_columns"])
        self.assertEqual(preview3["rows"][0]["phone_status"], "missing")

    def test_size_limits(self):
        big = "Name,Team\n" + "".join(f"P{i} X,T\n" for i in range(2001))
        self.upload(big, status=400)
        self.upload("", status=400)


class PhoneSelfServiceTest(BenchLinkCase):
    """phone-self-service"""

    def test_self_service_rules(self):
        csv_text = roster(skater("S1", "Pat", "One", "Aces", phone="403-555-0101", email="pat@example.com"),
                          skater("S2", "Lee", "Two", "Aces"))
        self.import_roster(csv_text)
        pat, lee, other, cap = self.member("pat"), self.member("lee"), self.member("other"), self.member("cap")
        self.link("Pat One", "pat")
        self.link("Lee Two", "lee")
        self.captain("Aces", "cap")
        me = pat.get(self.api("/me")).json()["player"]
        self.assertEqual((me["phone"], me["email"]), ("+14035550101", "pat@example.com"))
        r = self.patch_me(pat, {"phone": "(587) 555-0102"})
        self.assertEqual(r.json()["player"]["phone"], "+15875550102")
        self.assertEqual(self.patch_me(lee, {"phone": "12345"}).status, 400)
        self.assertEqual(self.patch_me(lee, {"available_as_sub": True}).status, 400)
        self.assertEqual(self.patch_me(lee, {"available_as_sub": "yes"}).status, 400)
        self.assertEqual(self.patch_me(lee, {"email": "x@example.com"}).status, 400)
        r = self.patch_me(lee, {"phone": "1-403-555-0103", "available_as_sub": True})
        self.assertEqual(r.status, 200)
        self.assertEqual((r.json()["player"]["phone"], r.json()["player"]["available_as_sub"]),
                         ("+14035550103", True))
        for viewer in (other, self.owner):
            text = viewer.get(self.api("/players")).text
            self.assert_no_contact(text, "5875550102", "4035550103", "pat@example.com")
            self.assertTrue(self.players(viewer)["Pat One"]["has_phone"])
            pid = self.players()["Lee Two"]["id"]
            single = viewer.get(self.api(f"/players/{pid}"))
            self.assertNotIn("phone\"", single.text.replace("has_phone\"", ""))
        pid = self.players()["Pat One"]["id"]
        for key, value in (("phone", "4035550100"), ("email", "a@example.com")):
            self.assertEqual(self.owner.request("PATCH", self.api(f"/players/{pid}"),
                                                json_body={key: value}).status, 400)
        self.assertEqual(cap.request("PATCH", self.api(f"/players/{pid}"), json_body={"jersey": "9"}).status, 403)
        self.assertEqual(self.patch_me(other, {"phone": "4035550100"}).status, 404)
        self.import_roster(roster(skater("S1", "Pat", "One", "Aces", phone="403-555-0101", email="pat@example.com"),
                                  skater("S2", "Lee", "Two", "Aces", phone="403-555-0999")))
        self.assertEqual(pat.get(self.api("/me")).json()["player"]["phone"], "+15875550102")
        self.assertEqual(lee.get(self.api("/me")).json()["player"]["phone"], "+14035550103")
        cleared = self.patch_me(lee, {"phone": ""}).json()["player"]
        self.assertEqual((cleared["phone"], cleared["available_as_sub"], cleared["has_phone"]), ("", False, False))

    def test_normalisation(self):
        norm = main.roster_import.normalize_phone
        self.assertEqual(norm("(403) 555-0101"), "+14035550101")
        self.assertEqual(norm("+1 403 555 0113"), "+14035550113")
        self.assertIsNone(norm("555-0104"))
        self.assertIsNone(norm("103-555-0101"))
        self.assertIsNone(norm("2-403-555-0101"))


class GamesAndSubsTest(BenchLinkCase):
    """games-and-available-subs"""

    def test_games_and_available_subs(self):
        three_team_setup(self)
        teams = self.teams()
        g1 = self.game("Aces", "Bears", rink="North Rink")
        self.game("Aces", "Bears", starts_at="2000-01-01T10:00")
        g2 = self.game("Comets", "Bears", starts_at="2099-02-01T21:00", rink="South Rink")
        p_a = self.users["p_a"]
        body = {"home_team_id": teams["Aces"]["id"], "away_team_id": teams["Bears"]["id"],
                "starts_at": "2099-01-01T20:00"}
        self.assertEqual(p_a.post(self.api("/games"), body).status, 403)
        self.assertEqual(self.owner.post(self.api("/games"), dict(body, away_team_id=teams["Aces"]["id"])).status, 400)
        self.assertEqual(self.owner.post(self.api("/games"), dict(body, starts_at="2099-02-30T20:00")).status, 400)
        self.assertEqual(self.owner.post(self.api("/games"), dict(body, starts_at="2099-01-01 20:00")).status, 400)
        self.assertEqual(self.owner.post(self.api("/games"), dict(body, home_team_id=str(teams["Aces"]["id"]))).status,
                         201)
        self.assertEqual(appkit.Client(main.app).get(self.api("/games")).status, 401)
        games = p_a.get(self.api("/games")).json()["games"]
        self.assertEqual([g["id"] for g in games][:1], [g1["id"]])
        self.assertNotIn("2000-01-01T10:00", [g["starts_at"] for g in games])
        self.assertEqual(games[0]["home_team"]["name"], "Aces")
        self.assertEqual(games[0]["rink"], "North Rink")
        self.assertEqual(games[0]["skaters"]["home"]["limit"], 8)
        self.assertEqual(len(p_a.get(self.api("/games?include_past=1")).json()["games"]), 4)
        self.assertEqual(p_a.get(self.api(f"/games/{g1['id']}")).status, 200)
        subs1 = p_a.get(self.api(f"/games/{g1['id']}/available-subs"))
        self.assertEqual([s["name"] for s in subs1.json()["subs"]], ["Cy Cole"])
        subs2 = p_a.get(self.api(f"/games/{g2['id']}/available-subs"))
        self.assertEqual([s["name"] for s in subs2.json()["subs"]], ["Alex Ames"])
        self.assert_no_contact(subs1.text + subs2.text, "403555", "@example.com")
        self.patch_me(self.users["p_c"], {"available_as_sub": False})
        self.assertEqual(p_a.get(self.api(f"/games/{g1['id']}/available-subs")).json()["subs"], [])


class RsvpLimitTest(BenchLinkCase):
    """rsvp-skater-limit"""

    def test_eight_skater_limit(self):
        rows = [skater(f"A{i}", f"Sk{i}", "Aces", "Aces", phone="") for i in range(1, 10)]
        rows.append(skater("AG", "Goalie", "Aces", "Aces", pos="G"))
        rows += [skater("B1", "Bo", "Banks", "Bears"), skater("C1", "Cy", "Cole", "Comets", phone="403-555-0121"),
                 skater("C2", "Cam", "Cruz", "Comets", phone="403-555-0122")]
        self.import_roster(roster(*rows))
        cap_a, cap_b, nine, bo = self.member("cap_a"), self.member("cap_b"), self.member("nine"), self.member("bo_b")
        cy, cam = self.member("cy_c"), self.member("cam")
        self.captain("Aces", "cap_a")
        self.captain("Bears", "cap_b")
        self.link("Sk9 Aces", "nine")
        self.link("Bo Banks", "bo_b")
        self.link("Cy Cole", "cy_c")
        self.link("Cam Cruz", "cam")
        self.patch_me(cy, {"available_as_sub": True})
        self.patch_me(cam, {"available_as_sub": True})
        game = self.game("Aces", "Bears")
        players = self.players()
        ids = [players[f"Sk{i} Aces"]["id"] for i in range(1, 10)]
        for pid in ids[:7]:
            self.assertEqual(self.rsvp(cap_a, game["id"], pid, "in").status, 200)
        r = self.rsvp(nine, game["id"], ids[8], "in")
        self.assertEqual(r.json()["game"]["skaters"]["home"]["count"], 8)
        self.assertEqual(self.rsvp(cap_a, game["id"], ids[7], "in").status, 409)
        self.assertEqual(self.rsvp(cap_a, game["id"], ids[0], "in").status, 200)  # already in: not a new skater
        teams = self.teams()
        self.assertEqual(self.request_sub(cap_a, game["id"], teams["Aces"]["id"], players["Cy Cole"]["id"]).status, 409)
        r = self.rsvp(cap_a, game["id"], players["Goalie Aces"]["id"], "in")
        self.assertEqual(r.json()["game"]["skaters"]["home"]["goalies_in"], 1)
        self.assertEqual(self.rsvp(cap_b, game["id"], ids[7], "in").status, 403)
        self.assertEqual(self.rsvp(bo, game["id"], ids[7], "in").status, 403)
        self.assertEqual(self.rsvp(cap_a, game["id"], players["Cy Cole"]["id"], "in").status, 400)
        self.assertEqual(self.rsvp(cap_a, game["id"], ids[7], "maybe").status, 400)
        r = self.rsvp(nine, game["id"], ids[8], "out")
        home = r.json()["game"]["skaters"]["home"]
        self.assertEqual((home["rsvp_in"], home["rsvp_out"], home["count"]), (7, 1, 7))
        sr = self.request_sub(cap_a, game["id"], teams["Aces"]["id"], players["Cy Cole"]["id"])
        self.assertEqual(sr.status, 201)
        self.assertEqual(cap_b.post(self.api(f"/sub-requests/{sr.json()['sub_request']['id']}/approve")).status, 200)
        home = cap_a.get(self.api(f"/games/{game['id']}")).json()["game"]["skaters"]["home"]
        self.assertEqual((home["confirmed_subs"], home["count"]), (1, 8))
        self.assertEqual(self.rsvp(cap_a, game["id"], ids[7], "in").status, 409)
        self.assertEqual(self.rsvp(cap_a, game["id"], ids[0], "out").status, 200)
        self.assertEqual(self.rsvp(cap_a, game["id"], ids[7], "in").status, 200)
        rsvps = cap_a.get(self.api(f"/games/{game['id']}/rsvps")).json()["rsvps"]
        self.assertEqual(len(rsvps), 10)

    def test_limit_blocks_confirming_approval(self):
        rows = [skater(f"A{i}", f"Sk{i}", "Aces", "Aces") for i in range(1, 9)]
        rows += [skater("B1", "Bo", "Banks", "Bears"), skater("C1", "Cy", "Cole", "Comets", phone="403-555-0121")]
        self.import_roster(roster(*rows))
        cap_a, cap_b, cy = self.member("cap_a"), self.member("cap_b"), self.member("cy_c")
        self.captain("Aces", "cap_a")
        self.captain("Bears", "cap_b")
        self.link("Cy Cole", "cy_c")
        self.patch_me(cy, {"available_as_sub": True})
        game, players, teams = self.game("Aces", "Bears"), self.players(), self.teams()
        for i in range(1, 8):
            self.rsvp(cap_a, game["id"], players[f"Sk{i} Aces"]["id"], "in")
        sr = self.request_sub(cap_a, game["id"], teams["Aces"]["id"], players["Cy Cole"]["id"]).json()["sub_request"]
        self.rsvp(cap_a, game["id"], players["Sk8 Aces"]["id"], "in")
        self.assertEqual(cap_b.post(self.api(f"/sub-requests/{sr['id']}/approve")).status, 409)
        self.assertEqual(cap_b.get(self.api(f"/sub-requests/{sr['id']}")).json()["sub_request"]["status"], "pending")


class TwoCaptainApprovalTest(BenchLinkCase):
    """two-captain-approval"""

    def test_two_captain_flow(self):
        three_team_setup(self)
        teams, players = self.teams(), self.players()
        cap_a, cap_b, cap_c, p_a = (self.users[n] for n in ("cap_a", "cap_b", "cap_c", "p_a"))
        g1 = self.game("Aces", "Bears", starts_at="2099-01-01T20:00")
        g2 = self.game("Aces", "Bears", starts_at="2099-01-08T20:00")
        cy = players["Cy Cole"]["id"]
        aces = teams["Aces"]["id"]
        for client in (p_a, cap_c, self.owner):
            self.assertEqual(self.request_sub(client, g1["id"], aces, cy).status, 403)
        r = self.request_sub(cap_a, g1["id"], aces, cy)
        self.assertEqual(r.status, 201)
        sr1 = r.json()["sub_request"]
        self.assertEqual((sr1["status"], sr1["approvals"]), ("pending", {"home": True, "away": False}))
        self.assertEqual(self.request_sub(cap_a, g1["id"], aces, cy).status, 409)
        approve = self.api(f"/sub-requests/{sr1['id']}/approve")
        self.assertEqual(cap_a.post(approve).status, 409)
        for client in (cap_c, p_a, self.users["p_c"], self.owner):
            self.assertEqual(client.post(approve).status, 403)
        self.assertEqual(cap_a.get(self.api(f"/games/{g1['id']}")).json()["game"]["skaters"]["home"]["confirmed_subs"],
                         0)
        sr2 = self.request_sub(cap_b, g2["id"], teams["Bears"]["id"], cy).json()["sub_request"]
        self.assertEqual(sr2["approvals"], {"home": False, "away": True})
        r = cap_b.post(approve)
        self.assertEqual(r.status, 200)
        self.assertEqual(r.json()["sub_request"]["status"], "confirmed")
        self.assertNotIn("contact", r.json()["sub_request"])
        self.assertEqual(cap_a.get(self.api(f"/games/{g1['id']}")).json()["game"]["skaters"]["home"]["confirmed_subs"],
                         1)
        self.assertEqual(cap_a.get(self.api(f"/sub-requests/{sr2['id']}")).json()["sub_request"]["status"], "pending")
        self.assertEqual(cap_a.get(self.api(f"/games/{g2['id']}")).json()["game"]["skaters"]["away"]["confirmed_subs"],
                         0)
        self.assertEqual(cap_b.post(approve).status, 409)
        r = cap_a.post(self.api(f"/sub-requests/{sr2['id']}/decline"))
        self.assertEqual(r.json()["sub_request"]["status"], "declined")
        self.assertEqual(cap_a.post(self.api(f"/sub-requests/{sr2['id']}/approve")).status, 409)
        self.assertEqual(cap_a.post(self.api(f"/sub-requests/{sr2['id']}/decline")).status, 409)
        l1 = cap_a.get(self.api(f"/games/{g1['id']}/sub-requests")).json()["sub_requests"]
        l2 = cap_a.get(self.api(f"/games/{g2['id']}/sub-requests")).json()["sub_requests"]
        self.assertEqual(([r["id"] for r in l1], [r["id"] for r in l2]), ([sr1["id"]], [sr2["id"]]))
        assignments = self.users["p_c"].get(self.api("/me")).json()["sub_assignments"]
        self.assertEqual([a["status"] for a in assignments], ["confirmed", "declined"])


class ContactRevealTest(BenchLinkCase):
    """contact-after-confirmation"""

    def test_contact_only_after_confirmation(self):
        three_team_setup(self)
        teams, players = self.teams(), self.players()
        cap_a, cap_b, cap_c, p_a, p_c = (self.users[n] for n in ("cap_a", "cap_b", "cap_c", "p_a", "p_c"))
        self.patch_me(p_c, {"phone": "403-555-0130"})
        self.link("Bee Burns", "cap_b")
        self.patch_me(cap_b, {"phone": "403-555-0140"})
        secrets = ("+14035550130", "cy@example.com", "+14035550140")
        g1 = self.game("Aces", "Bears")
        sr = self.request_sub(cap_a, g1["id"], teams["Aces"]["id"], players["Cy Cole"]["id"]).json()["sub_request"]
        path = self.api(f"/sub-requests/{sr['id']}")
        for client in (cap_a, cap_b, p_c):
            text = client.get(path).text
            self.assertNotIn("contact", text)
            if client is not p_c:
                self.assert_no_contact(text, *secrets)
        self.assert_no_contact(cap_a.get(self.api(f"/players/{players['Cy Cole']['id']}")).text, *secrets)
        approval = cap_b.post(path + "/approve")
        self.assertNotIn("contact", approval.text)
        self.assert_no_contact(approval.text, *secrets)
        for client in (cap_a, cap_b):
            contact = client.get(path).json()["sub_request"]["contact"]
            self.assertEqual((contact["sub"]["phone"], contact["sub"]["email"]), ("+14035550130", "cy@example.com"))
        contact = p_c.get(path).json()["sub_request"]["contact"]
        away = [c for c in contact["captains"] if c["team_id"] == teams["Bears"]["id"]][0]
        self.assertEqual((away["username"], away["phone"]), ("cap_b", "+14035550140"))
        self.assertEqual(contact["captains"][0]["team_id"], teams["Aces"]["id"])
        for client in (p_a, cap_c, self.owner):
            data = client.get(path)
            self.assertEqual(data.json()["sub_request"]["status"], "confirmed")
            self.assertNotIn("contact", data.text)
            self.assert_no_contact(data.text, *secrets)
        self.assert_no_contact(cap_a.get(self.api(f"/games/{g1['id']}/sub-requests")).text, *secrets)
        self.assert_no_contact(cap_a.get(self.api(f"/players/{players['Cy Cole']['id']}")).text, *secrets)
        self.assert_no_contact(self.owner.get(self.api("/players")).text, *secrets)
        desk = f"/leagues/{self.gid}/games/{g1['id']}"
        self.assert_no_contact(p_a.get(desk).text, *secrets)
        self.assertIn("+14035550130", cap_a.get(desk).text)


class SubEligibilityTest(BenchLinkCase):
    """sub-eligibility"""

    def test_eligibility_rules(self):
        three_team_setup(self)
        self.member("p_c2")
        self.link("Cam Cruz", "p_c2")
        self.member("p_a_off")
        teams, players = self.teams(), self.players()
        cap_a, cap_b, cap_c, p_c = (self.users[n] for n in ("cap_a", "cap_b", "cap_c", "p_c"))
        g1 = self.game("Aces", "Bears")
        g2 = self.game("Comets", "Bears")
        aces, comets = teams["Aces"]["id"], teams["Comets"]["id"]
        self.assertEqual(self.request_sub(cap_a, g1["id"], aces, players["Alex Ames"]["id"]).status, 400)
        self.assertEqual(self.request_sub(cap_a, g1["id"], aces, players["Bo Banks"]["id"]).status, 400)
        self.assertEqual(self.request_sub(cap_a, g1["id"], aces, players["Cam Cruz"]["id"]).status, 400)
        self.assertEqual(self.request_sub(cap_a, g1["id"], aces, 999999).status, 400)
        self.assertEqual(self.request_sub(cap_a, g1["id"], aces, "abc").status, 400)
        self.assertEqual(self.request_sub(cap_c, g1["id"], comets, players["Cy Cole"]["id"]).status, 400)
        self.assertEqual(self.request_sub(cap_c, g2["id"], comets, players["Cy Cole"]["id"]).status, 400)
        self.assertEqual(self.request_sub(cap_c, g2["id"], comets, players["Bo Banks"]["id"]).status, 400)
        self.assertEqual(self.request_sub(cap_c, g2["id"], comets, str(players["Alex Ames"]["id"])).status, 201)
        sr = self.request_sub(cap_a, g1["id"], str(aces), players["Cy Cole"]["id"])
        self.assertEqual(sr.status, 201)
        rid = sr.json()["sub_request"]["id"]
        self.patch_me(p_c, {"available_as_sub": False})
        self.assertEqual(cap_b.post(self.api(f"/sub-requests/{rid}/approve")).status, 400)
        self.assertEqual(cap_b.get(self.api(f"/sub-requests/{rid}")).json()["sub_request"]["status"], "pending")
        self.patch_me(p_c, {"available_as_sub": True})
        self.assertEqual(cap_b.post(self.api(f"/sub-requests/{rid}/approve")).json()["sub_request"]["status"],
                         "confirmed")
        l1 = cap_a.get(self.api(f"/games/{g1['id']}/sub-requests")).json()["sub_requests"]
        l2 = cap_a.get(self.api(f"/games/{g2['id']}/sub-requests")).json()["sub_requests"]
        self.assertEqual([r["player"]["name"] for r in l1], ["Cy Cole"])
        self.assertEqual([r["player"]["name"] for r in l2], ["Alex Ames"])


class RolesTest(BenchLinkCase):
    """roles-and-sign-in"""

    def test_roles(self):
        self.import_roster(roster(skater("A1", "Alex", "Ames", "Aces"), skater("B1", "Bo", "Banks", "Bears")))
        anon = appkit.Client(main.app)
        self.assertEqual(anon.get(self.api("/games")).status, 401)
        self.assertEqual(anon.get(self.api("/players")).status, 401)
        self.assertIn(anon.post(self.api("/imports"), {"csv": "Name,Team\nA B,C\n"}).status, (401, 403))
        self.assertEqual(self.owner.post(self.api("/imports"), {"csv": "Name,Team\nA B,C\n"}, csrf=False).status, 403)
        plain, co, cap = self.member("plain"), self.member("coadmin", role="admin"), self.member("cap")
        self.member("linked")
        self.user("outsider")
        teams, players = self.teams(), self.players()
        aces, bears, alex = teams["Aces"]["id"], teams["Bears"]["id"], players["Alex Ames"]["id"]
        attempts = [("POST", "/teams", {"name": "X"}),
                    ("POST", "/games", {"home_team_id": aces, "away_team_id": bears, "starts_at": "2099-01-01T20:00"}),
                    ("POST", f"/teams/{aces}/captains", {"username": "plain"}),
                    ("POST", f"/players/{alex}/link", {"username": "plain"}),
                    ("PATCH", f"/players/{alex}", {"jersey": "3"}),
                    ("GET", "/imports", None)]
        for method, path, payload in attempts:
            self.assertEqual(plain.request(method, self.api(path), json_body=payload).status, 403, path)
        self.assertEqual(self.upload("Name,Team\nNew Person,Aces\n", client=co)["import"]["summary"]["create"], 1)
        self.assertEqual(co.post(self.api("/teams"), {"name": "Comets"}).status, 201)
        self.assertEqual(co.post(self.api("/teams"), {"name": "  comets "}).status, 409)
        self.assertEqual(co.post(self.api("/teams"), {"name": ""}).status, 400)
        self.assertEqual(self.owner.post(self.api(f"/teams/{aces}/captains"), {"username": "outsider"}).status, 404)
        self.captain("Aces", "cap")
        self.assertEqual(self.owner.post(self.api(f"/teams/{aces}/captains"), {"username": "cap"}).status, 409)
        self.link("Alex Ames", "linked")
        self.assertEqual(self.owner.post(self.api(f"/players/{alex}/link"), {"username": "plain"}).status, 409)
        bo = players["Bo Banks"]["id"]
        self.assertEqual(self.owner.post(self.api(f"/players/{bo}/link"), {"username": "linked"}).status, 409)
        self.assertEqual(self.owner.post(self.api(f"/players/{bo}/link"), {"username": "outsider"}).status, 404)
        self.assertEqual(cap.post(self.api(f"/teams/{bears}/captains"), {"username": "plain"}).status, 403)
        self.assertEqual(cap.post(self.api("/games"), attempts[1][2]).status, 403)
        me = cap.get(self.api("/me")).json()
        self.assertEqual((me["captain_of"], me["is_league_admin"], me["role"]), ([aces], False, "member"))
        self.assertTrue(co.get(self.api("/me")).json()["is_league_admin"])
        self.assertEqual(self.users["linked"].get(self.api("/me")).json()["player"]["id"], alex)
        self.assertEqual(self.users["outsider"].get(self.api("/me")).status, 404)
        team = self.owner.get(self.api(f"/teams/{aces}")).json()["team"]
        self.assertEqual(team["captains"], [{"user_id": team["captains"][0]["user_id"], "username": "cap"}])
        self.assertEqual(self.owner.request("DELETE", self.api(f"/teams/{aces}")).status, 409)
        comets = self.teams()["Comets"]["id"]
        self.assertEqual(self.owner.request("DELETE", self.api(f"/teams/{comets}")).json(), {"deleted": comets})
        unlinked = self.owner.request("DELETE", self.api(f"/players/{alex}/link")).json()["player"]
        self.assertFalse(unlinked["linked"])


class LeagueIsolationTest(BenchLinkCase):
    """league-isolation and cross-league-records"""

    def test_team_isolation(self):
        other = self.user("owner2")
        gid2 = other.post("/api/groups", {"name": "Other"}).json()["group"]["id"]
        t1 = self.owner.post(self.api("/teams"), {"name": "marker-one"}).json()["team"]
        t2 = other.post(f"/api/groups/{gid2}/teams", {"name": "marker-two"}).json()["team"]
        for client, own, foreign_gid, foreign in ((self.owner, self.gid, gid2, t2), (other, gid2, self.gid, t1)):
            for gid in (own, foreign_gid):
                self.assertEqual(client.get(f"/api/groups/{gid}/teams/{foreign['id']}").status, 404)
                self.assertEqual(client.request("PATCH", f"/api/groups/{gid}/teams/{foreign['id']}",
                                                json_body={"name": "hacked"}).status, 404)
                self.assertEqual(client.request("DELETE", f"/api/groups/{gid}/teams/{foreign['id']}").status, 404)
            self.assertEqual(client.get(f"/api/groups/{foreign_gid}/teams").status, 404)
            self.assertNotIn(foreign["name"], client.get(f"/api/groups/{own}/teams").text)
        outsider = self.user("outsider")
        self.assertEqual(outsider.get(self.api(f"/teams/{t1['id']}")).status, 404)
        self.assertEqual(appkit.Client(main.app).get(self.api("/teams")).status, 401)
        self.assertEqual(self.owner.get(self.api(f"/teams/{t1['id']}")).json()["team"]["name"], "marker-one")

    def test_cross_league_records(self):
        three_team_setup(self)
        teams, players = self.teams(), self.players()
        g1 = self.game("Aces", "Bears")
        sr = self.request_sub(self.users["cap_a"], g1["id"], teams["Aces"]["id"],
                              players["Cy Cole"]["id"]).json()["sub_request"]
        imp = self.upload(roster(skater("A9", "New", "Guy", "Aces")))["import"]
        o2 = self.user("owner2")
        gid2 = o2.post("/api/groups", {"name": "Other"}).json()["group"]["id"]
        o2.post(f"/api/groups/{gid2}/teams", {"name": "Zed"})
        self.user("member2")
        self.assertEqual(o2.post(f"/api/groups/{gid2}/members", {"username": "member2"}).status, 201)
        alex, aces = players["Alex Ames"]["id"], teams["Aces"]["id"]
        for gid in (self.gid, gid2):
            base = f"/api/groups/{gid}"
            checks = [o2.get(f"{base}/players/{alex}"), o2.get(f"{base}/games/{g1['id']}"),
                      o2.get(f"{base}/sub-requests/{sr['id']}"), o2.get(f"{base}/imports/{imp['id']}"),
                      o2.post(f"{base}/imports/{imp['id']}/commit"),
                      o2.post(f"{base}/players/{alex}/link", {"username": "owner2"}),
                      o2.request("PUT", f"{base}/games/{g1['id']}/rsvps/{alex}", json_body={"status": "in"}),
                      o2.post(f"{base}/sub-requests/{sr['id']}/approve"), o2.get(f"{base}/teams/{aces}"),
                      o2.post(f"{base}/teams/{aces}/captains", {"username": "owner2"}),
                      o2.get(f"{base}/games/{g1['id']}/available-subs"),
                      o2.get(f"{base}/players?team_id={aces}")]
            for response in checks:
                self.assertEqual(response.status, 404, response.text)
                self.assertNotIn("Ames", response.text)
        r = o2.post(f"/api/groups/{gid2}/games", {"home_team_id": aces, "away_team_id": teams["Bears"]["id"],
                                                  "starts_at": "2099-01-01T20:00"})
        self.assertIn(r.status, (400, 404))
        self.assertEqual(self.users["member2"].get(self.api("/games")).status, 404)
        self.assertEqual(self.users["p_a"].get(f"/api/groups/{gid2}/players").status, 404)
        preview = o2.post(f"/api/groups/{gid2}/imports", {"csv": roster(
            skater("A1", "Other", "Person", "Zed", email="alex@example.com"),
            skater("B1", "Second", "Person", "Zed"))}).json()["import"]
        self.assertEqual((preview["summary"]["create"], preview["summary"]["update"], preview["summary"]["unchanged"]),
                         (2, 0, 0))
        o2.post(f"/api/groups/{gid2}/imports/{preview['id']}/commit")
        names2 = {p["name"] for p in o2.get(f"/api/groups/{gid2}/players").json()["players"]}
        self.assertEqual(names2, {"Other Person", "Second Person"})
        self.assertNotIn("Other Person", self.players())
        self.assertEqual([t["name"] for t in o2.get(f"/api/groups/{gid2}/teams").json()["teams"]], ["Zed"])
        self.assertEqual(self.owner.get(self.api(f"/sub-requests/{sr['id']}")).json()["sub_request"]["status"],
                         "pending")


class ScreensTest(BenchLinkCase):
    """ui-screens"""

    def test_screens(self):
        three_team_setup(self)
        self.import_roster(roster(skater("X1", "<script>alert(1)</script>", "Evil", "Aces")))
        g1 = self.game("Aces", "Bears", rink="Glacier Arena")
        p_a, p_b = self.users["p_a"], self.users["p_b"]
        league = p_a.get(f"/leagues/{self.gid}").text
        for text in ("Aces", "Bears", "Comets", "Glacier Arena"):
            self.assertIn(text, league)
        desk = p_a.get(f"/leagues/{self.gid}/games/{g1['id']}").text
        self.assertIn("Cy Cole", desk)
        self.assertIn("BenchLink does not send messages", desk)
        admin_roster = self.owner.get(f"/leagues/{self.gid}/roster").text
        member_roster = p_b.get(f"/leagues/{self.gid}/roster").text
        for text in (admin_roster, member_roster):
            self.assertNotIn("<script>alert(1)</script>", text)
            self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", text)
        self.assertIn("Upload a SportsEngine roster export (CSV)", admin_roster)
        self.assertNotIn("Upload a SportsEngine roster export (CSV)", member_roster)
        profile = p_a.get(f"/leagues/{self.gid}/me").text
        self.assertIn("We do not text this number yet", profile)
        self.assertIn("+14035550101", profile)
        pages = [league, desk, admin_roster, member_roster, profile]
        for text in pages:
            self.assertNotIn("Connected to SportsEngine", text)
            self.assertNotIn("Message sent", text)
            self.assert_no_contact(text, "+14035550121", "cy@example.com", "+14035550111")
        js = p_a.get("/static/benchlink.js")
        self.assertEqual((js.status, js.content_type), (200, "text/javascript"))
        self.assertIn("X-CSRF-Token", js.text)
        for path in ("/", f"/leagues/{self.gid}", f"/leagues/{self.gid}/roster", f"/leagues/{self.gid}/me",
                     f"/leagues/{self.gid}/games/{g1['id']}"):
            r = appkit.Client(main.app).get(path)
            self.assertEqual((r.status, dict(r.headers).get("Location")), (303, "/signin"))
        stranger = self.user("stranger")
        stranger.post("/api/groups", {"name": "Elsewhere"})
        for path in (f"/leagues/{self.gid}", f"/leagues/{self.gid}/roster", f"/leagues/{self.gid}/me",
                     f"/leagues/{self.gid}/games/{g1['id']}"):
            self.assertEqual(stranger.get(path).status, 404)
        imp = self.upload(roster(skater("Z1", "Zed", "Zero", "Aces", phone="403-555-0155")))["import"]
        page_text = self.owner.get(f"/leagues/{self.gid}/imports/{imp['id']}").text
        self.assertIn("Commit import", page_text)
        self.assertNotIn("4035550155", page_text)
        self.assertEqual(p_a.get(f"/leagues/{self.gid}/imports/{imp['id']}").status, 403)
        self.assertIn("Elsewhere", stranger.get("/").text)


class RestartPersistenceTest(BenchLinkCase):
    """restart-persistence"""

    TABLES = ("teams", "team_captains", "players", "games", "rsvps", "sub_requests", "roster_imports", "users",
              "user_groups", "memberships")

    def snapshot(self):
        conn = sqlite3.connect(Path(self.data_dir) / "app.db")
        try:
            return {t: conn.execute(f"SELECT * FROM {t} ORDER BY 1, 2").fetchall() for t in self.TABLES}
        finally:
            conn.close()

    def test_state_survives_reopen(self):
        three_team_setup(self)
        teams, players = self.teams(), self.players()
        g1 = self.game("Aces", "Bears")
        cap_a, cap_b = self.users["cap_a"], self.users["cap_b"]
        self.rsvp(cap_a, g1["id"], players["Alex Ames"]["id"], "in")
        sr = self.request_sub(cap_a, g1["id"], teams["Aces"]["id"], players["Cy Cole"]["id"]).json()["sub_request"]
        cap_b.post(self.api(f"/sub-requests/{sr['id']}/approve"))
        before = self.snapshot()
        main.app.open(self.data_dir)  # a fresh start on the same data directory
        self.assertEqual(self.snapshot(), before)
        again = appkit.Client(main.app)
        self.assertEqual(again.post("/api/signin", {"username": "cap_a", "password": "correct horse battery"}).status,
                         200)
        contact = again.get(self.api(f"/sub-requests/{sr['id']}")).json()["sub_request"]["contact"]
        self.assertEqual(contact["sub"]["phone"], "+14035550121")
        game = again.get(self.api(f"/games/{g1['id']}")).json()["game"]
        self.assertEqual((game["skaters"]["home"]["rsvp_in"], game["skaters"]["home"]["confirmed_subs"]), (1, 1))
        self.assertEqual(self.owner.get(self.api("/imports")).json()["imports"][0]["status"], "committed")


class PreferredPositionsTest(BenchLinkCase):
    """revision-preferred-positions"""

    def test_preferred_positions_api(self):
        three_team_setup(self)
        players = self.players()
        p_c, cap_a = self.users["p_c"], self.users["cap_a"]
        self.assertEqual(players["Cy Cole"]["preferred_positions"], [])
        r = self.patch_me(p_c, {"preferred_positions": ["D", "LW"]})
        self.assertEqual(r.status, 200, r.text)
        self.assertEqual(r.json()["player"]["preferred_positions"], ["LW", "D"])
        for bad in (["D", "XX"], ["D", "D"], "D", ["lw"], [1], None, ["C", "LW", "RW", "D", "G", "C"]):
            self.assertEqual(self.patch_me(p_c, {"preferred_positions": bad}).status, 400, bad)
        cy, bo = players["Cy Cole"]["id"], players["Bo Banks"]["id"]
        self.assertEqual(cap_a.get(self.api(f"/players/{cy}")).json()["player"]["preferred_positions"], ["LW", "D"])
        self.assertEqual(cap_a.get(self.api(f"/players/{bo}")).json()["player"]["preferred_positions"], [])
        me = p_c.get(self.api("/me")).json()["player"]
        self.assertEqual((me["preferred_positions"], me["phone"]), (["LW", "D"], "+14035550121"))
        self.assertEqual(self.players(cap_a)["Cy Cole"]["preferred_positions"], ["LW", "D"])
        g1 = self.game("Aces", "Bears")
        subs = cap_a.get(self.api(f"/games/{g1['id']}/available-subs")).json()["subs"]
        self.assertEqual([(s["name"], s["preferred_positions"]) for s in subs], [("Cy Cole", ["LW", "D"])])
        self.assertEqual(self.owner.get(self.api("/games")).status, 200)
        # Admins cannot set it for a player; imports leave it alone.
        self.assertEqual(self.owner.request("PATCH", self.api(f"/players/{cy}"),
                                            json_body={"preferred_positions": ["C"]}).status, 400)
        self.import_roster(roster(skater("C1", "Cy", "Cole", "Comets", pos="Defense", phone="403-555-0121",
                                         email="cy@example.com")))
        self.assertEqual(p_c.get(self.api("/me")).json()["player"]["preferred_positions"], ["LW", "D"])
        with sqlite3.connect(Path(self.data_dir) / "app.db") as conn:
            self.assertEqual(conn.execute("SELECT preferred_positions FROM players WHERE id=?", (cy,)).fetchone()[0],
                             "LW,D")
        cleared = self.patch_me(p_c, {"preferred_positions": []}).json()["player"]
        self.assertEqual((cleared["preferred_positions"], cleared["available_as_sub"]), ([], True))


class RevisionUpgradeTest(unittest.TestCase):
    """revision-preferred-positions: a version 1 database upgrades in place and keeps every row."""

    TABLES = ("teams", "team_captains", "players", "games", "rsvps", "sub_requests", "roster_imports", "users",
              "user_groups", "memberships")

    def snapshot(self, path):
        conn = sqlite3.connect(path)
        try:
            return {t: conn.execute(f"SELECT * FROM {t} ORDER BY 1, 2").fetchall() for t in self.TABLES}
        finally:
            conn.close()

    def test_v1_database_upgrades(self):
        migrations = HERE.parent / "db" / "migrations"
        with tempfile.TemporaryDirectory() as tmp:
            v1_dir, data_dir = Path(tmp) / "v1_migrations", Path(tmp) / "data"
            v1_dir.mkdir()
            (v1_dir / "0001_benchlink.sql").write_bytes((migrations / "0001_benchlink.sql").read_bytes())
            v1 = appkit.App("BenchLink v1", v1_dir).open(data_dir)
            clients = {}
            for name in ("owner", "cap_a", "cap_b", "sub"):
                clients[name] = appkit.Client(v1)
                self.assertEqual(clients[name].signup(name).status, 201)
            gid = clients["owner"].post("/api/groups", {"name": "League"}).json()["group"]["id"]
            for name in ("cap_a", "cap_b", "sub"):
                clients["owner"].post(f"/api/groups/{gid}/members", {"username": name, "role": "member"})
            db = data_dir / "app.db"
            conn = sqlite3.connect(db)
            with conn:
                uid = {r[1]: r[0] for r in conn.execute("SELECT id, username FROM users")}
                teams = [conn.execute("INSERT INTO teams (group_id, name, name_key, created_at) VALUES (?, ?, ?, 1)",
                                      (gid, n, n.lower())).lastrowid for n in ("Aces", "Bears", "Comets")]
                for team, user in ((teams[0], "cap_a"), (teams[1], "cap_b")):
                    conn.execute("INSERT INTO team_captains (group_id, team_id, user_id, created_at) VALUES (?, ?, ?, 1)",
                                 (gid, team, uid[user]))
                ames = conn.execute("INSERT INTO players (group_id, team_id, first_name, last_name, created_at) "
                                    "VALUES (?, ?, 'Alex', 'Ames', 1)", (gid, teams[0])).lastrowid
                sub = conn.execute("INSERT INTO players (group_id, team_id, first_name, last_name, phone, phone_source, "
                                   "available_as_sub, user_id, email, created_at) VALUES (?, ?, 'Cy', 'Cole', "
                                   "'+14035550121', 'self', 1, ?, 'cy@example.com', 1)",
                                   (gid, teams[2], uid["sub"])).lastrowid
                game = conn.execute("INSERT INTO games (group_id, home_team_id, away_team_id, starts_at, rink, "
                                    "created_at) VALUES (?, ?, ?, '2099-01-01T20:00', 'North', 1)",
                                    (gid, teams[0], teams[1])).lastrowid
                rid = conn.execute("INSERT INTO sub_requests (group_id, game_id, team_id, player_id, status, "
                                   "requested_by, home_approved_by, created_at) VALUES (?, ?, ?, ?, 'pending', ?, ?, 1)",
                                   (gid, game, teams[0], sub, uid["cap_a"], uid["cap_a"])).lastrowid
                conn.execute("INSERT INTO roster_imports (group_id, status, csv_text, created_by, created_at) "
                             "VALUES (?, 'committed', '', ?, 1)", (gid, uid["owner"]))
            conn.close()
            before = self.snapshot(db)

            main.app.open(data_dir)  # the current revision starts on the same database
            after = self.snapshot(db)
            self.assertEqual(set(after), set(before))
            for table in self.TABLES:
                rows = after[table]
                if table == "players":  # + preferred_positions (0002) and captain_on_link (0003)
                    self.assertTrue(all(r[-2:] == ("", 0) for r in rows))
                    rows = [r[:-2] for r in rows]
                if table == "teams":  # + division (0003)
                    self.assertTrue(all(r[-1] == "" for r in rows))
                    rows = [r[:-1] for r in rows]
                self.assertEqual(rows, before[table], table)
            conn = sqlite3.connect(db)
            try:
                applied = [r[0] for r in conn.execute("SELECT name FROM schema_version WHERE component='app' "
                                                      "ORDER BY version")]
            finally:
                conn.close()
            self.assertEqual(applied, [p.stem.split("_", 1)[1] for p in sorted(migrations.glob("*.sql"))])
            self.assertEqual(applied, ["benchlink", "preferred_positions", "divisions_and_captains"])

            api = f"/api/groups/{gid}"
            sub_client = appkit.Client(main.app)
            sub_client.post("/api/signin", {"username": "sub", "password": "correct horse battery"})
            r = sub_client.request("PATCH", api + "/me/player", json_body={"preferred_positions": ["D", "LW"]})
            self.assertEqual(r.json()["player"]["preferred_positions"], ["LW", "D"])
            self.assertEqual(sub_client.request("PATCH", api + "/me/player",
                                                json_body={"preferred_positions": ["XX"]}).status, 400)
            self.assertEqual(sub_client.request("PATCH", api + "/me/player",
                                                json_body={"preferred_positions": ["D", "D"]}).status, 400)
            cap = appkit.Client(main.app)
            cap.post("/api/signin", {"username": "cap_a", "password": "correct horse battery"})
            self.assertEqual(cap.get(f"{api}/players/{sub}").json()["player"]["preferred_positions"], ["LW", "D"])
            self.assertEqual(cap.get(f"{api}/players/{ames}").json()["player"]["preferred_positions"], [])
            me = sub_client.get(api + "/me").json()["player"]
            self.assertEqual((me["phone"], me["available_as_sub"], me["preferred_positions"]),
                             ("+14035550121", True, ["LW", "D"]))
            self.assertEqual(cap.get(f"{api}/sub-requests/{rid}").json()["sub_request"]["status"], "pending")
            admin = appkit.Client(main.app)
            admin.post("/api/signin", {"username": "owner", "password": "correct horse battery"})
            self.assertEqual([g["id"] for g in admin.get(api + "/games").json()["games"]], [game])


class DivisionsAndCaptainsImportTest(BenchLinkCase):
    """roster-import-divisions-and-captains"""

    FIRST = excel_roster(
        ("Avery", "Lindqvist", "(403) 555-0101", "Lakeside Loons", "Upper", "Y", "note, with comma"),
        ("Brennan", "Okoro", "403-555-0102", "Lakeside Loons", "upper", "", ""),
        ("Imogen", "Castellan", "", "Summit Sabres", "Upper", "yes", ""),
        ("Quill", "Ashcombe", "403.555.0118", "Prairie Owls", "Lower", "x", ""),
        ("Rhea", "Dunmore", "", "Prairie Owls", "Lower", "maybe", ""),
        ("Soren", "Blackwood", "(403) 555-0119", "Prairie Owls", " Lower ", "", ""),
    )

    def test_sample_league_roster_upload(self):
        preview = self.upload(LEAGUE_SAMPLE)["import"]
        self.assertEqual(preview["summary"], {"total": 28, "create": 28, "update": 0, "unchanged": 0,
                                              "duplicate": 0, "error": 0})
        self.assertEqual(preview["ignored_columns"], [])
        self.assertEqual([c["field"] for c in preview["columns"]],
                         ["first_name", "last_name", "phone", "team", "division", "captain"])
        self.assertEqual(preview["rows"][2]["name"], "Mary Kate Fairbanks")
        self.assertEqual(sum(r["captain"] for r in preview["rows"]), 4)
        self.assertEqual({r["division"] for r in preview["rows"]}, {"Upper", "Lower"})
        self.assertEqual({r["phone_status"] for r in preview["rows"]}, {"present", "missing"})
        self.assert_no_contact(json.dumps(preview), "4035550101", "555-0101", "403.555")
        result = self.owner.post(self.api(f"/imports/{preview['id']}/commit")).json()
        self.assertEqual([t["name"] for t in result["teams"]],
                         ["Lakeside Loons", "Summit Sabres", "Prairie Owls", "Creekside Otters"])
        teams = self.teams()
        self.assertEqual({n: t["division"] for n, t in teams.items()},
                         {"Lakeside Loons": "Upper", "Summit Sabres": "Upper", "Prairie Owls": "Lower",
                          "Creekside Otters": "Lower"})
        self.assertEqual([t["captains"] for t in teams.values()], [[], [], [], []])
        marked = sorted(n for n, p in self.players().items() if p["captain_on_link"])
        self.assertEqual(marked, ["Avery Lindqvist", "Imogen Castellan", "Quill Ashcombe", "Yara Pellston"])
        again = self.upload(LEAGUE_SAMPLE)["import"]["summary"]
        self.assertEqual((again["create"], again["unchanged"], again["error"]), (0, 28, 0))

    def test_divisions_and_errors(self):
        preview = self.upload(self.FIRST)["import"]
        self.assertEqual(preview["summary"], {"total": 6, "create": 6, "update": 0, "unchanged": 0,
                                              "duplicate": 0, "error": 0})
        self.assertEqual(preview["ignored_columns"], ["Notes"])
        rows = preview["rows"]
        self.assertEqual([r["division"] for r in rows], ["Upper", "upper", "Upper", "Lower", "Lower", "Lower"])
        self.assertEqual([r["captain"] for r in rows], [True, False, True, True, False, False])
        self.assertEqual(rows[4]["warnings"], ["captain cell not recognised"])
        self.assertEqual(rows[0]["warnings"], [])
        self.assert_no_contact(json.dumps(preview), "4035550101", "555-0101")
        self.assertEqual(self.owner.get(self.api("/teams")).json()["teams"], [])
        self.owner.post(self.api(f"/imports/{preview['id']}/commit"))
        teams = self.teams()
        self.assertEqual([(n, t["division"]) for n, t in teams.items()],
                         [("Lakeside Loons", "Upper"), ("Prairie Owls", "Lower"), ("Summit Sabres", "Upper")])
        players = self.players()
        self.assertEqual({n: p["captain_on_link"] for n, p in players.items()},
                         {"Avery Lindqvist": True, "Brennan Okoro": False, "Imogen Castellan": True,
                          "Quill Ashcombe": True, "Rhea Dunmore": False, "Soren Blackwood": False})

        split = excel_roster(("Tom", "One", "", "Split Team", "Upper", "", ""),
                             ("Tom", "One", "", "Split Team", "Upper", "", ""),
                             ("Una", "Two", "", "split  team", "Lower", "", ""),
                             ("Val", "Three", "", "Whole Team", "Upper", "", ""))
        rows = self.upload(split)["import"]["rows"]
        self.assertEqual([r["action"] for r in rows], ["error", "error", "error", "create"])
        self.assertTrue(all("team in two divisions" in r["errors"] for r in rows[:3]))

        long_div, ok_div = "D" * 41, "E" * 40
        third = excel_roster(("Wes", "A", "", "Long Team", long_div, "", ""),
                             ("Xan", "B", "", "Long Team", long_div, "", ""),
                             ("Yara", "C", "", "Fine Team", ok_div, "", ""))
        preview3 = self.upload(third)["import"]
        rows = preview3["rows"]
        self.assertEqual([r["action"] for r in rows], ["error", "error", "create"])
        self.assertEqual(rows[0]["errors"], ["division too long"])
        self.assertEqual(rows[1]["errors"], ["division too long"])
        self.assertEqual(rows[2]["division"], ok_div)
        self.owner.post(self.api(f"/imports/{preview3['id']}/commit"))
        teams = self.teams()
        self.assertEqual(teams["Fine Team"]["division"], ok_div)
        self.assertNotIn("Long Team", teams)

        # The latest committed import wins; a blank division leaves the team alone.
        self.import_roster(excel_roster(("Avery", "Lindqvist", "", "Lakeside Loons", "Lower", "", ""),
                                        ("Brennan", "Okoro", "", "Lakeside Loons", "", "", "")))
        self.assertEqual(self.teams()["Lakeside Loons"]["division"], "Lower")
        self.import_roster(excel_roster(("Avery", "Lindqvist", "", "Lakeside Loons", "", "", "")))
        self.assertEqual(self.teams()["Lakeside Loons"]["division"], "Lower")

    def test_captain_on_link(self):
        self.import_roster(self.FIRST)
        cap1, early, plain = self.member("cap1"), self.member("early"), self.member("plain")
        teams, players = self.teams(), self.players()
        loons, owls = teams["Lakeside Loons"]["id"], teams["Prairie Owls"]["id"]
        # Not linked yet: no captain rights.
        self.assertEqual(cap1.get(self.api("/me")).json()["captain_of"], [])
        self.import_roster(excel_roster(("Zed", "Sub", "403-555-0150", "Summit Sabres", "Upper", "", "")))
        self.member("zed")
        self.link("Zed Sub", "zed")
        self.assertEqual(self.patch_me(self.users["zed"], {"available_as_sub": True}).status, 200)
        game = self.game("Lakeside Loons", "Prairie Owls")
        zed = self.players()["Zed Sub"]["id"]
        self.assertEqual(self.request_sub(cap1, game["id"], loons, zed).status, 403)
        r = self.owner.post(self.api(f"/players/{players['Avery Lindqvist']['id']}/link"), {"username": "cap1"})
        self.assertEqual(r.status, 200)
        self.assertEqual(set(r.json()["player"]), set(players["Avery Lindqvist"]))
        self.assertEqual([c["username"] for c in self.teams()["Lakeside Loons"]["captains"]], ["cap1"])
        self.assertEqual(cap1.get(self.api("/me")).json()["captain_of"], [loons])
        self.assertEqual(self.request_sub(cap1, game["id"], loons, zed).status, 201)
        # Linked first, marked later: rights only once the marking import is committed.
        self.link("Soren Blackwood", "early")
        marking = excel_roster(("Soren", "Blackwood", "", "Prairie Owls", "Lower", "C", ""))
        preview = self.upload(marking)["import"]
        self.assertEqual(preview["summary"]["update"], 1)
        self.assertEqual(early.get(self.api("/me")).json()["captain_of"], [])
        self.owner.post(self.api(f"/imports/{preview['id']}/commit"))
        self.assertEqual(early.get(self.api("/me")).json()["captain_of"], [owls])
        # An unmarked player's link grants nothing.
        self.link("Brennan Okoro", "plain")
        self.assertEqual(plain.get(self.api("/me")).json()["captain_of"], [])
        # A blank captain cell never removes a marker or a captaincy.
        blank = excel_roster(("Avery", "Lindqvist", "", "Lakeside Loons", "Upper", "", ""),
                             ("Soren", "Blackwood", "", "Prairie Owls", "Lower", "", ""))
        result = self.import_roster(blank)
        self.assertEqual(result["import"]["summary"]["unchanged"], 2)
        self.assertEqual(cap1.get(self.api("/me")).json()["captain_of"], [loons])
        self.assertEqual(early.get(self.api("/me")).json()["captain_of"], [owls])
        avery = players["Avery Lindqvist"]["id"]
        self.assertEqual(self.owner.request("DELETE", self.api(f"/players/{avery}/link")).status, 200)
        self.assertEqual(cap1.get(self.api("/me")).json()["captain_of"], [loons])
        self.assertEqual(self.owner.post(self.api(f"/players/{avery}/link"), {"username": "cap1"}).status, 200)
        self.assertEqual([c["username"] for c in self.teams()["Lakeside Loons"]["captains"]], ["cap1"])
        self.assertTrue(self.players()["Avery Lindqvist"]["captain_on_link"])
        # Moving the player does not move the captaincy; re-linking grants for the current team.
        self.owner.request("PATCH", self.api(f"/players/{avery}"), json_body={"team_id": owls})
        self.assertEqual(cap1.get(self.api("/me")).json()["captain_of"], [loons])
        self.owner.request("DELETE", self.api(f"/players/{avery}/link"))
        self.owner.post(self.api(f"/players/{avery}/link"), {"username": "cap1"})
        self.assertEqual(cap1.get(self.api("/me")).json()["captain_of"], sorted([loons, owls]))
        # Admins still remove captains by hand.
        uid = self.teams()["Lakeside Loons"]["captains"][0]["user_id"]
        self.assertEqual(self.owner.request("DELETE", self.api(f"/teams/{loons}/captains/{uid}")).status, 200)
        self.assertEqual(cap1.get(self.api("/me")).json()["captain_of"], [owls])

    def test_team_division_patch(self):
        tid = self.owner.post(self.api("/teams"), {"name": "Aces"}).json()["team"]["id"]
        path = self.api(f"/teams/{tid}")
        self.assertEqual(self.owner.get(path).json()["team"]["division"], "")
        r = self.owner.request("PATCH", path, json_body={"division": "  Upper   Tier "})
        self.assertEqual((r.status, r.json()["team"]["division"], r.json()["team"]["name"]), (200, "Upper Tier", "Aces"))
        for bad in (None, 3, ["Upper"], True, "F" * 41):
            self.assertEqual(self.owner.request("PATCH", path, json_body={"division": bad}).status, 400, bad)
        r = self.owner.request("PATCH", path, json_body={"division": "G" * 40})
        self.assertEqual((r.status, r.json()["team"]["division"]), (200, "G" * 40))
        r = self.owner.request("PATCH", path, json_body={"division": "Lower", "name": "Aces Two"})
        self.assertEqual((r.json()["team"]["division"], r.json()["team"]["name"]), ("Lower", "Aces Two"))
        self.assertEqual(self.owner.request("PATCH", path, json_body={"division": ""}).json()["team"]["division"], "")
        self.assertEqual(self.member("plain").request("PATCH", path, json_body={"division": "Upper"}).status, 403)
        self.assertEqual(self.owner.request("PATCH", path, json_body={"tier": "Upper"}).status, 400)

    def test_screens_group_by_division(self):
        self.import_roster(self.FIRST)
        self.owner.post(self.api("/teams"), {"name": "Loose Pucks"})
        self.owner.post(self.api("/teams"), {"name": "Beer League"})
        self.owner.request("PATCH", self.api(f"/teams/{self.teams()['Beer League']['id']}"),
                           json_body={"division": "Masters"})
        viewer = self.member("viewer")
        self.member("cap1")
        self.link("Imogen Castellan", "cap1")
        roster_page = self.owner.get(f"/leagues/{self.gid}/roster").text
        league_page = viewer.get(f"/leagues/{self.gid}").text
        for text in (roster_page, league_page):
            positions = [text.index(f">{heading}<") for heading in ("Upper", "Lower", "Masters", "No division")]
            self.assertEqual(positions, sorted(positions))
            self.assertLess(text.index("Lakeside Loons"), text.index("Prairie Owls"))
            self.assertNotIn("4035550101", text)
        self.assertIn("Upload a SportsEngine roster export (CSV)", roster_page)
        self.assertIn("In Excel, use File → Save As → CSV UTF-8, then upload.", roster_page)
        avery_row = roster_page[roster_page.index("Avery Lindqvist"):]
        self.assertTrue(avery_row.split("</td>", 1)[0].endswith("Captain (on link)</em>"))
        imogen_row = roster_page[roster_page.index("Imogen Castellan"):]
        self.assertNotIn("Captain (on link)", imogen_row.split("</td>", 1)[0])
        self.assertEqual(roster_page.count("Captain (on link)"), 2)  # Avery and Quill; Imogen is linked
        self.assertIn('name="division"', roster_page)
        member_roster = viewer.get(f"/leagues/{self.gid}/roster").text
        self.assertNotIn('name="division"', member_roster)
        preview = self.upload(self.FIRST)["import"]
        page_text = self.owner.get(f"/leagues/{self.gid}/imports/{preview['id']}").text
        self.assertIn("captain cell not recognised", page_text)
        self.assertNotIn("4035550101", page_text)


class ContactCardPerViewerTest(BenchLinkCase):
    """contact-card-per-viewer"""

    def test_card_shows_the_other_people(self):
        self.import_roster(roster(
            skater("H1", "Pat", "Plain", "Aces", phone="403-555-0161", email="pat@example.com"),
            skater("B1", "Andy", "Away", "Bears", phone="403-555-0140", email="andy@example.com"),
            skater("C1", "Cy", "Cole", "Comets", phone="403-555-0130", email="cy@example.com"),
        ))
        for name in ("cap_home", "cap_home2", "cap_away", "sub", "plain"):
            self.member(name)
        self.captain("Aces", "cap_home")
        self.captain("Aces", "cap_home2")
        self.captain("Bears", "cap_away")
        self.link("Andy Away", "cap_away")
        self.link("Cy Cole", "sub")
        self.link("Pat Plain", "plain")
        self.patch_me(self.users["sub"], {"available_as_sub": True})
        teams, players = self.teams(), self.players()
        game = self.game("Aces", "Bears")
        sr = self.request_sub(self.users["cap_home"], game["id"], teams["Aces"]["id"],
                              players["Cy Cole"]["id"]).json()["sub_request"]
        self.assertEqual(self.users["cap_away"].post(self.api(f"/sub-requests/{sr['id']}/approve")).json()
                         ["sub_request"]["status"], "confirmed")
        desk = f"/leagues/{self.gid}/games/{game['id']}"

        home = self.users["cap_home"].get(desk).text
        self.assertIn("Your sub", home)
        self.assertIn("Other captain", home)
        self.assertNotIn("Your captains", home)
        self.assertIn("Cy Cole: +14035550130, cy@example.com", home)
        self.assertIn("andy@example.com", home)
        self.assertIn("Aces captain cap_home2: no phone on file, no email on file", home)
        self.assertNotIn("captain cap_home:", home)
        self.assertIn("BenchLink does not send messages", home)

        away = self.users["cap_away"].get(desk).text
        self.assertIn("Your sub", away)
        self.assertIn("Other captain", away)
        self.assertNotIn("Your captains", away)
        self.assertIn("Aces captain cap_home: no phone on file", away)
        self.assertNotIn("andy@example.com", away)
        self.assertNotIn("+14035550140", away)

        sub = self.users["sub"].get(desk).text
        self.assertIn("Your captains", sub)
        self.assertNotIn("Your sub", sub)
        self.assertNotIn("Other captain", sub)
        self.assertIn("Bears captain Andy Away: +14035550140, andy@example.com", sub)
        self.assertIn("Aces captain cap_home: no phone on file", sub)
        self.assert_no_contact(sub, "+14035550130", "cy@example.com")

        plain = self.users["plain"].get(desk).text
        for text in ("Contact details", "Your sub", "Your captains", "Other captain"):
            self.assertNotIn(text, plain)
        self.assert_no_contact(plain, "+14035550130", "cy@example.com", "+14035550140", "andy@example.com")
        # The JSON contact section is unchanged: the sub and every captain of both teams.
        contact = self.users["sub"].get(self.api(f"/sub-requests/{sr['id']}")).json()["sub_request"]["contact"]
        self.assertEqual(contact["sub"]["phone"], "+14035550130")
        self.assertEqual([c["username"] for c in contact["captains"]], ["cap_home", "cap_home2", "cap_away"])

    def test_captain_who_is_also_the_sub(self):
        self.import_roster(roster(skater("B1", "Bo", "Banks", "Bears"),
                                  skater("C1", "Cy", "Cole", "Comets", phone="403-555-0130", email="cy@example.com")))
        both, cap_b = self.member("both"), self.member("cap_b")
        self.owner.post(self.api("/teams"), {"name": "Aces"})
        self.captain("Aces", "both")
        self.captain("Bears", "cap_b")
        self.link("Cy Cole", "both")
        self.patch_me(both, {"available_as_sub": True})
        teams, players = self.teams(), self.players()
        game = self.game("Aces", "Bears")
        sr = self.request_sub(both, game["id"], teams["Aces"]["id"], players["Cy Cole"]["id"]).json()["sub_request"]
        cap_b.post(self.api(f"/sub-requests/{sr['id']}/approve"))
        text = both.get(f"/leagues/{self.gid}/games/{game['id']}").text
        for heading in ("Your sub", "Other captain", "Your captains"):
            self.assertIn(heading, text)
        self.assert_no_contact(text, "+14035550130", "cy@example.com", "captain both:")
        self.assertIn("Bears captain cap_b", text)


class RevisionDivisionsUpgradeTest(unittest.TestCase):
    """revision-divisions-and-captains: a revision 2 database upgrades in place and keeps every row."""

    TABLES = RevisionUpgradeTest.TABLES

    def snapshot(self, path):
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        try:
            return {t: [dict(r) for r in conn.execute(f"SELECT * FROM {t} ORDER BY 1, 2")] for t in self.TABLES}
        finally:
            conn.close()

    def test_revision2_database_upgrades(self):
        migrations = HERE.parent / "db" / "migrations"
        with tempfile.TemporaryDirectory() as tmp:
            r2_dir, data_dir = Path(tmp) / "r2_migrations", Path(tmp) / "data"
            r2_dir.mkdir()
            for name in ("0001_benchlink.sql", "0002_preferred_positions.sql"):
                (r2_dir / name).write_bytes((migrations / name).read_bytes())
            r2 = appkit.App("BenchLink revision 2", r2_dir).open(data_dir)
            clients = {}
            for name in ("owner", "cap_a", "cap_b", "sub"):
                clients[name] = appkit.Client(r2)
                self.assertEqual(clients[name].signup(name).status, 201)
            gid = clients["owner"].post("/api/groups", {"name": "League"}).json()["group"]["id"]
            for name in ("cap_a", "cap_b", "sub"):
                clients["owner"].post(f"/api/groups/{gid}/members", {"username": name, "role": "member"})
            db = data_dir / "app.db"
            conn = sqlite3.connect(db)
            with conn:
                uid = {r[1]: r[0] for r in conn.execute("SELECT id, username FROM users")}
                teams = {n: conn.execute("INSERT INTO teams (group_id, name, name_key, created_at) VALUES (?, ?, ?, 1)",
                                         (gid, n, n.lower())).lastrowid for n in ("Aces", "Bears", "Comets")}
                for team, user in (("Aces", "cap_a"), ("Bears", "cap_b"), ("Comets", "cap_b")):
                    conn.execute("INSERT INTO team_captains (group_id, team_id, user_id, created_at) VALUES (?, ?, ?, 1)",
                                 (gid, teams[team], uid[user]))
                rows = [("A1", "Alex", "Ames", "Aces", "", None, 0, ""), ("A2", "Ari", "Abbott", "Aces", "", None, 0, ""),
                        ("B1", "Bo", "Banks", "Bears", "", None, 0, ""), ("C2", "Cam", "Cruz", "Comets", "", None, 0, ""),
                        ("C1", "Cy", "Cole", "Comets", "+14035550121", uid["sub"], 1, "LW,D")]
                pid = {}
                for ext, first, last, team, phone, user, avail, prefs in rows:
                    pid[first] = conn.execute(
                        "INSERT INTO players (group_id, team_id, first_name, last_name, external_id, phone, phone_source,"
                        " available_as_sub, user_id, preferred_positions, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?,"
                        " ?, 1)", (gid, teams[team], first, last, ext, phone, "self" if phone else "", avail, user,
                                   prefs)).lastrowid
                game = conn.execute("INSERT INTO games (group_id, home_team_id, away_team_id, starts_at, rink, "
                                    "created_at) VALUES (?, ?, ?, '2099-01-01T20:00', 'North', 1)",
                                    (gid, teams["Aces"], teams["Bears"])).lastrowid
                rid = conn.execute("INSERT INTO sub_requests (group_id, game_id, team_id, player_id, status, "
                                   "requested_by, home_approved_by, created_at) VALUES (?, ?, ?, ?, 'pending', ?, ?, 1)",
                                   (gid, game, teams["Aces"], pid["Cy"], uid["cap_a"], uid["cap_a"])).lastrowid
                conn.execute("INSERT INTO roster_imports (group_id, status, csv_text, created_by, created_at) "
                             "VALUES (?, 'committed', '', ?, 1)", (gid, uid["owner"]))
            conn.close()
            before = self.snapshot(db)

            main.app.open(data_dir)  # revision 3 starts on the same database
            after = self.snapshot(db)
            for table in self.TABLES:
                old_rows = before[table]
                new_rows = [{k: v for k, v in r.items() if k in (old_rows[0] if old_rows else r)}
                            for r in after[table]]
                self.assertEqual(new_rows, old_rows, table)
            self.assertTrue(all(r["division"] == "" for r in after["teams"]))
            self.assertTrue(all(r["captain_on_link"] == 0 for r in after["players"]))
            conn = sqlite3.connect(db)
            try:
                applied = [r[0] for r in conn.execute("SELECT name FROM schema_version WHERE component='app' "
                                                      "ORDER BY version")]
            finally:
                conn.close()
            self.assertEqual(applied, [p.stem.split("_", 1)[1] for p in sorted(migrations.glob("*.sql"))])
            self.assertEqual(applied, ["benchlink", "preferred_positions", "divisions_and_captains"])

            api = f"/api/groups/{gid}"
            signed = {}
            for name in ("owner", "cap_a", "cap_b", "sub"):
                signed[name] = appkit.Client(main.app)
                self.assertEqual(signed[name].post("/api/signin", {"username": name,
                                                                   "password": "correct horse battery"}).status, 200)
            self.assertEqual(signed["cap_a"].get(f"{api}/sub-requests/{rid}").json()["sub_request"]["status"], "pending")
            me = signed["sub"].get(api + "/me").json()
            self.assertEqual((me["player"]["phone"], me["player"]["available_as_sub"], me["player"]["preferred_positions"],
                              me["player"]["captain_on_link"], me["captain_of"]),
                             ("+14035550121", True, ["LW", "D"], False, []))
            listed = {t["name"]: t for t in signed["cap_a"].get(api + "/teams").json()["teams"]}
            self.assertEqual({n: t["division"] for n, t in listed.items()}, {"Aces": "", "Bears": "", "Comets": ""})
            self.assertEqual([c["username"] for c in listed["Comets"]["captains"]], ["cap_b"])
            self.assertEqual(signed["cap_a"].get(api + "/me").json()["captain_of"], [teams["Aces"]])

            csv_text = ("Member ID,First Name,Last Name,Team Name,League,Captain\n"
                        "A1,Alex,Ames,Aces,Upper,\nA2,Ari,Abbott,Aces,Upper,\nB1,Bo,Banks,Bears,Lower,\n"
                        "C2,Cam,Cruz,Comets,Lower,\nC1,Cy,Cole,Comets,Lower,Y\n")
            preview = signed["owner"].post(api + "/imports", {"csv": csv_text}).json()["import"]
            self.assertEqual((preview["summary"]["create"], preview["summary"]["error"], preview["summary"]["update"]),
                             (0, 0, 1))
            self.assertEqual(signed["owner"].post(f"{api}/imports/{preview['id']}/commit").status, 200)
            listed = {t["name"]: t for t in signed["cap_a"].get(api + "/teams").json()["teams"]}
            self.assertEqual({n: t["division"] for n, t in listed.items()},
                             {"Aces": "Upper", "Bears": "Lower", "Comets": "Lower"})
            self.assertEqual([c["username"] for c in listed["Comets"]["captains"]], ["cap_b", "sub"])
            me = signed["sub"].get(api + "/me").json()
            self.assertEqual((me["captain_of"], me["player"]["preferred_positions"], me["player"]["phone"]),
                             ([teams["Comets"]], ["LW", "D"], "+14035550121"))
            self.assertEqual(signed["cap_a"].get(api + "/me").json()["captain_of"], [teams["Aces"]])


class SchemaTest(unittest.TestCase):
    def test_schema_file_matches_migrations(self):
        with tempfile.TemporaryDirectory() as tmp:
            main.app.open(tmp)
            conn = appkit.connect(Path(tmp) / "app.db")
            try:
                dump = appkit.schema_dump(conn)
            finally:
                conn.close()
        schema = (HERE.parent / "db" / "schema.sql").read_text("utf-8")
        self.assertEqual(schema.split("\n", 1)[1], dump)


if __name__ == "__main__":
    unittest.main()
