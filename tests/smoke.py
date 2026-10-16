"""Plays a whole tournament through the HTTP API.  Run:  python tests/smoke.py
Starts its own server on port 8765 with a throw-away data folder."""
import json, os, shutil, subprocess, sys, tempfile, time, urllib.request, urllib.error
from http.cookiejar import CookieJar

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP = tempfile.mkdtemp()
shutil.copytree(ROOT, TMP + "/app", ignore=shutil.ignore_patterns("data", "__pycache__", "tests"))
ENV = dict(os.environ, PORT="8765", ADMIN_PIN="test", HOST="127.0.0.1")
URL = "http://127.0.0.1:8765"
PASS = FAIL = 0


def start():
    p = subprocess.Popen([sys.executable, "app.py"], cwd=TMP + "/app", env=ENV, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(50):
        try: urllib.request.urlopen(URL + "/api/health"); return p
        except Exception: time.sleep(.1)
    raise SystemExit("server didn't start")


jar = CookieJar()
op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))


def call(path, body=None, anon=False):
    o = urllib.request.build_opener() if anon else op
    req = urllib.request.Request(URL + path, data=None if body is None else json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    try:
        r = o.open(req); return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


n = [0]
def act(type, **kw):
    n[0] += 1
    return call("/api/action", {"type": type, "aid": f"t{n[0]}", **kw})


def check(name, cond):
    global PASS, FAIL
    PASS += cond; FAIL += (not cond)
    print(("  ok   " if cond else "  FAIL ") + name)


def admin(): return call("/api/admin")[1]
def cur(): return admin()["current"]


def clear_board(side_expected=None):
    c = cur()
    for i in range(len(c["answers"])):
        if not c["answers"][i]["revealed"]:
            assert act("correct", idx=i)[0] == 200


proc = start()
try:
    check("unauthenticated action rejected", act("start_match", id="m1")[0] == 401)
    check("wrong pin rejected", call("/api/login", {"pin": "nope"})[0] == 401)
    check("login ok", call("/api/login", {"pin": "test"})[0] == 200)
    check("screen API is public", call("/api/screen", anon=True)[0] == 200)
    check("final can't start early", act("start_match", id="m3")[0] == 400)

    # ── Semifinal 1: A (even questions) vs B
    check("start m1", act("start_match", id="m1")[0] == 200)
    check("can't start m2 while m1 live", act("start_match", id="m2")[0] == 400)
    scr = call("/api/screen", anon=True)[1]
    check("screen hides unrevealed answers", all("text" not in a for a in scr["current"]["answers"]))
    clear_board()                                    # q1: A clears the board
    c = cur(); check("pool scoring: board cleared → A gets the full pot", c["scores"]["a"] == sum(a["points"] for a in c["answers"]) and c["pool"] == 0 and c["phase"] == "resolved")
    check("duplicate reveal blocked", act("correct", idx=0)[0] == 400)
    check("undo restores", act("undo")[0] == 200 and cur()["phase"] == "play" and sum(a["revealed"] for a in cur()["answers"]) == len(cur()["answers"]) - 1)
    clear_board()
    act("next_question")
    clear_board()                                    # q2: B (active) clears
    act("next_question")
    act("correct", idx=0)                            # q3: A gets pot 38 …
    for _ in range(3): act("wrong")                  # … strikes out → steal for B
    c = cur(); check("3 strikes → steal phase", c["phase"] == "steal" and c["stealer"] == "b")
    check("steal success accepted", act("correct", idx=1)[0] == 200)
    c = cur(); check("question resolved after steal", c["phase"] == "resolved" and c["pool"] == 0)
    check("stealer takes the question pot", c["scores"]["b"] > 0)
    for _ in range(3, admin()["settings"]["per_match"]):
        act("next_question")
        clear_board()
    M1 = dict(cur()["scores"])
    check("finish m1", act("finish")[0] in (200, 400))
    m = admin()["bracket"]
    if m[0]["status"] != "done":
        print("        m1 tied or unresolved:", cur()["scores"])
    check("m1 completed", m[0]["status"] == "done")
    check("finalist 1 advanced", m[2]["teams"][0] in ("A", "B"))
    check("completed match is read-only", act("correct", idx=0)[0] == 400)
    check("can't restart m1", act("start_match", id="m1")[0] == 400)
    check("final still blocked", act("start_match", id="m3")[0] == 400)

    # ── restart server mid-way: state must persist
    proc.terminate(); proc.wait(); proc = start()
    jar.clear(); call("/api/login", {"pin": "test"})
    check("m1 survives restart", admin()["bracket"][0]["status"] == "done")

    # ── Semifinal 2 – isolation + tie-break
    check("start m2", act("start_match", id="m2")[0] == 200)
    c = cur(); check("m2 starts fresh", c["scores"] == {"a": 0, "b": 0} and c["qi"] == 0)
    q_m2 = c["question"]
    check("m1 result untouched", admin()["bracket"][0]["scores"] == M1)
    for q in range(admin()["settings"]["per_match"]):
        clear_board()                                # A, B, A  → A wins
        if q < admin()["settings"]["per_match"] - 1: act("next_question")
    if cur()["scores"]["a"] == cur()["scores"]["b"]:
        act("adjust", side="a", delta=1)
    check("finish m2", act("finish")[0] == 200)
    b = admin()["bracket"]
    check("final has both winners", None not in b[2]["teams"] and b[2]["names"][0] != "TBD")

    # ── Grand Final
    check("start final", act("start_match", id="m3")[0] == 200)
    c = cur(); check("final starts fresh", c["scores"] == {"a": 0, "b": 0} and c["label"] == "Grand Final")
    check("different questions than m2", c["question"] != q_m2)
    for q in range(admin()["settings"]["per_match"]):
        clear_board()
        if q < admin()["settings"]["per_match"] - 1: act("next_question")
    if cur()["scores"]["a"] == cur()["scores"]["b"]:
        act("adjust", side="a", delta=1)
    check("finish final", act("finish")[0] == 200)
    a = admin()
    check("champion crowned", a["champion"] is not None and a["status"] == "TOURNAMENT_COMPLETED")
    check("audit log recorded history", len(a["audit"]) > 10)

    # ── tie-break on a fresh tournament
    check("reset needs confirm", act("reset", confirm="no")[0] == 400)
    check("reset ok", act("reset", confirm="RESET")[0] == 200)
    act("start_match", id="m1")
    for q in range(admin()["settings"]["per_match"]):
        clear_board()
        if q < admin()["settings"]["per_match"] - 1: act("next_question")
    act("adjust", side="b", delta=cur()["scores"]["a"] - cur()["scores"]["b"])   # force a tie
    c = cur(); check("scores tied", c["scores"]["a"] == c["scores"]["b"])
    check("tie can't be finished / no arbitrary winner", act("finish")[0] == 400 and admin()["bracket"][0]["status"] == "live")
    check("tie-break question added", act("tiebreak")[0] == 200 and cur()["tiebreak"] is True)
    clear_board()
    check("tie-break decides winner", act("finish")[0] == 200 and admin()["bracket"][0]["winner"] is not None)
finally:
    proc.terminate()
    shutil.rmtree(TMP, ignore_errors=True)
print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
