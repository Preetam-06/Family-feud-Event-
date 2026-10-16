#!/usr/bin/env python3
"""Family Feud 1v1 tournament — one Python file, standard library only.

Run:   python app.py
Admin: http://localhost:8000/admin     (PIN: env ADMIN_PIN, default feud2026)
Screen:http://localhost:8000/screen
"""
import copy, hmac, json, os, re, secrets, shutil, socket, threading, time, traceback, uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

BASE = Path(__file__).resolve().parent
DATA = Path(os.environ.get("FEUD_DATA_DIR", str(BASE / "data")))
WEB = BASE / "static"
PIN = os.environ.get("ADMIN_PIN", "feud2026")
HOST = os.environ.get("HOST", "0.0.0.0")
REQUESTED_PORT = int(os.environ.get("PORT", "8000"))
PORT = REQUESTED_PORT
DEBUG = os.environ.get("DEBUG", "").lower() in {"1", "true", "yes", "on"}

LOCK = threading.RLock()          # serialises every state change
COND = threading.Condition()      # wakes SSE listeners
VERSION = 1
TOKENS, FAILS, PENDING_BACKUPS = set(), {}, []
SIDES = ("a", "b")
GAME_KEYS = ["qi", "questions", "scores", "strikes", "active", "phase", "pool", "stealer", "owner"]

T, Q, AUDIT = {}, [], []         # tournament, question bank, audit log


# ───────────────────────── storage ─────────────────────────
def now():
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def read_json(path, default=None):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text("utf-8"))
    except Exception:
        bak = path.with_name(path.name + ".bak")
        if bak.exists():
            try:
                d = json.loads(bak.read_text("utf-8"))
                print(f"! {path.name} unreadable — recovered from {bak.name}")
                return d
            except Exception:
                pass
        raise SystemExit(f"{path} is corrupted and no valid .bak exists. "
                         f"Restore a file from data/backups/ — refusing to reset anything.")


def write_json(path, obj):
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2, ensure_ascii=False)
        f.flush(); os.fsync(f.fileno())
    if path.exists():
        shutil.copy2(path, path.with_name(path.name + ".bak"))
    os.replace(tmp, path)          # atomic swap


def save():
    write_json(DATA / "tournament.json", T)
    write_json(DATA / "questions.json", Q)
    write_json(DATA / "audit.json", AUDIT)


def backup(tag):
    shutil.copy2(DATA / "tournament.json", DATA / "backups" / f"tournament-{time.strftime('%Y%m%d-%H%M%S')}-{tag}.json")


# ───────────────────────── seed data ─────────────────────────
SEED = [
    ("Name something people take with them when they go on a vacation.", [("Clothes", 28), ("Camera", 22), ("Money", 18), ("Phone", 14), ("Snacks", 5), ("Toiletries", 13)]),
    ("Name a reason people are late.", [("Traffic", 38), ("Overslept", 27), ("Alarm didn't ring", 15), ("Car trouble", 12), ("Forgot something", 3), ("Weather", 5)]),
    ("Name something found in a school bag.", [("Books", 35), ("Pen / Pencil", 25), ("Lunch box", 18), ("Water bottle", 12), ("Calculator", 5), ("Notebook", 5)]),
    ("Name a food people eat at the movies.", [("Popcorn", 45), ("Candy", 20), ("Soda", 15), ("Nachos", 12), ("Hot dog", 3), ("Ice cream", 5)]),
    ("Name something you do first thing in the morning.", [("Brush teeth", 32), ("Check phone", 26), ("Drink water", 18), ("Use bathroom", 14), ("Make coffee", 5), ("Take a shower", 5)]),
    ("Name a popular pizza topping.", [("Pepperoni", 38), ("Cheese", 22), ("Mushrooms", 16), ("Sausage", 14), ("Onion", 5), ("Olives", 5)]),
    ("Name an animal people keep as a pet.", [("Dog", 42), ("Cat", 32), ("Fish", 12), ("Bird", 8), ("Hamster", 1), ("Rabbit", 5)]),
    ("Name something you find in a kitchen.", [("Fridge", 30), ("Stove", 25), ("Sink", 20), ("Microwave", 15), ("Knife", 5), ("Toaster", 5)]),
    ("Name a place people go to relax.", [("Beach", 34), ("Home", 24), ("Spa", 18), ("Park", 14), ("Mountains", 5), ("Library", 5)]),
    ("Name something that is round.", [("Ball", 33), ("Wheel", 25), ("Coin", 18), ("Clock", 14), ("Plate", 5), ("Orange", 5)]),
    ("Name a superhero.", [("Superman", 30), ("Batman", 28), ("Spider-Man", 20), ("Iron Man", 12), ("Hulk", 5), ("Wonder Woman", 5)]),
    ("Name a fruit that is red.", [("Apple", 40), ("Strawberry", 28), ("Cherry", 16), ("Watermelon", 10), ("Raspberry", 1), ("Red grape", 5)]),
]


def seed_questions():
    return [{"id": f"q{i+1}", "text": t, "enabled": True,
             "answers": [{"text": a, "points": p} for a, p in ans]} for i, (t, ans) in enumerate(SEED)]


def new_tournament():
    return {
        "id": uuid.uuid4().hex[:8], "schema": 1, "created": now(), "completed": None,
        "settings": {"event": "Family Feud", "round": "Round 3", "strike_limit": 3, "steal": True, "mode": "pool", "per_match": 10},
        "teams": [{"id": "A", "name": "Team Alpha"}, {"id": "B", "name": "Team Nova"},
                  {"id": "C", "name": "Team Phoenix"}, {"id": "D", "name": "Team Titans"}],
        "matches": [
            {"id": "m1", "label": "Semifinal 1", "teams": ["A", "B"], "status": "pending"},
            {"id": "m2", "label": "Semifinal 2", "teams": ["C", "D"], "status": "pending"},
            {"id": "m3", "label": "Grand Final", "teams": [None, None], "status": "pending"},
        ],
        "current": None, "spotlight": "auto", "champion": None, "fx": {"id": 0, "type": None}, "seen": [],
    }


def load():
    global T, Q, AUDIT
    DATA.mkdir(exist_ok=True); (DATA / "backups").mkdir(exist_ok=True)
    T = read_json(DATA / "tournament.json")
    Q = read_json(DATA / "questions.json")
    AUDIT = read_json(DATA / "audit.json", [])
    if T is None:
        T = new_tournament()
    if not Q:
        bank = BASE / "data" / "feud-30-questions.json"
        Q = read_json(bank, seed_questions()) if bank.exists() else seed_questions()
    # Older local state used three questions per match. A not-yet-started
    # tournament can safely be migrated to the complete 30-question format.
    if not started_any() and len(Q) >= 30 and T["settings"].get("per_match", 0) != 10:
        T["settings"]["per_match"] = 10
    save()


# ───────────────────────── helpers ─────────────────────────
class Err(Exception):
    pass


def need(cond, msg):
    if not cond:
        raise Err(msg)


def other(s):
    return "b" if s == "a" else "a"


def team_name(tid):
    return next((t["name"] for t in T["teams"] if t["id"] == tid), "TBD") if tid else "TBD"


def get_match(mid):
    return next((m for m in T["matches"] if m["id"] == mid), None)


def live_match():
    return next((m for m in T["matches"] if m["status"] == "live"), None)


def fx(kind, **kw):
    T["fx"] = {"id": T["fx"]["id"] + 1, "type": kind, **kw}


def log(kind, m=None, **d):
    AUDIT.append({"id": uuid.uuid4().hex[:10], "ts": now(), "type": kind, "match": m["id"] if m else None, **d})


def add(m, side, pts, reason):
    if pts == 0:
        return
    m["scores"][side] += pts
    log("score", m, team=m["teams"][SIDES.index(side)], points=pts, reason=reason, question=m["qi"] + 1)


def push_undo(m):
    m["undo"].append(copy.deepcopy({k: m[k] for k in GAME_KEYS}))
    del m["undo"][:-80]


def a_int(v, lo, hi, msg):
    try:
        v = int(v)
    except Exception:
        raise Err(msg)
    need(lo <= v <= hi, msg)
    return v


def tstatus():
    s1, s2, fin = T["matches"]
    if T["champion"]: return "TOURNAMENT_COMPLETED"
    if fin["status"] == "live": return "FINAL_IN_PROGRESS"
    if s1["status"] == "done" and s2["status"] == "done": return "FINAL_READY"
    if "live" in (s1["status"], s2["status"]): return "SEMIFINALS_IN_PROGRESS"
    if "done" in (s1["status"], s2["status"]): return "WAITING_FOR_FINALISTS"
    return "NOT_STARTED"


def started_any():
    return any(m["status"] != "pending" for m in T["matches"])


def live():
    m = live_match()
    need(m, "No live match — start one first")
    return m


def gate(m):
    need(not m["paused"], "Match is paused — resume first")


def is_resolved(m):
    return m["phase"] == "resolved"


def clean_q(d, keep_id=None):
    need(isinstance(d, dict), "Bad question")
    text = str(d.get("text", "")).strip()
    need(3 <= len(text) <= 200, "Question must be 3–200 characters")
    ans = d.get("answers")
    need(isinstance(ans, list) and 1 <= len(ans) <= 6, "Add 1–6 answers")
    out = []
    for a in ans:
        need(isinstance(a, dict), "Bad answer")
        t = str(a.get("text", "")).strip()
        need(1 <= len(t) <= 40, "Each answer needs 1–40 characters")
        out.append({"text": t, "points": a_int(a.get("points"), 1, 100, "Points must be 1–100")})
    qid = keep_id or d.get("id")
    if not (isinstance(qid, str) and re.fullmatch(r"[\w-]{1,20}", qid)):
        qid = "q" + uuid.uuid4().hex[:6]
    return {"id": qid, "text": text, "enabled": bool(d.get("enabled", True)), "answers": out}


# ───────────────────────── actions ─────────────────────────
ACTIONS = {}


def action(f):
    ACTIONS[f.__name__] = f
    return f


@action
def start_match(a):
    m = get_match(a.get("id"))
    need(m, "No such match")
    need(m["status"] == "pending", "This match was already started")
    need(not live_match(), "Another match is still live")
    if m["id"] == "m3":
        need(all(x["status"] == "done" for x in T["matches"][:2]) and None not in m["teams"],
             "Both semifinals must finish before the Grand Final")
    bank = [q for q in Q if q.get("enabled", True) and q["answers"]]
    n = T["settings"]["per_match"]
    need(len(bank) >= n, f"Need at least {n} enabled questions (have {len(bank)})")
    start = T["matches"].index(m) * n
    qs = [{"id": q["id"], "text": q["text"], "answers": [{"text": x["text"], "points": x["points"], "revealed": False} for x in q["answers"]]}
          for q in (bank[(start + i) % len(bank)] for i in range(n))]
    s = T["settings"]
    m.update(status="live", started=now(), questions=qs, qi=0, scores={"a": 0, "b": 0}, strikes={"a": 0, "b": 0},
             active="a", phase="play", pool=0, stealer=None, owner=None, paused=False, undo=[], winner=None, winner_side=None,
             rules={"mode": s["mode"], "strike_limit": s["strike_limit"], "steal": s["steal"]})
    T["current"], T["spotlight"] = m["id"], "auto"
    fx("start")
    log("match_start", m, teams=m["teams"], questions=[q["id"] for q in qs], rules=m["rules"])
    PENDING_BACKUPS.append("start-" + m["id"])


@action
def pause(a):
    m = live(); m["paused"] = True; log("pause", m)


@action
def resume(a):
    m = live(); m["paused"] = False; log("resume", m)


@action
def set_active(a):
    m = live(); gate(m)
    need(m["phase"] == "play", "Can only switch the answering team while playing")
    side = a.get("side"); need(side in SIDES, "Bad team")
    m["active"] = side; log("set_active", m, side=side)


@action
def correct(a):
    m = live(); gate(m)
    need(m["phase"] in ("play", "steal"), "This question is already resolved")
    ans = m["questions"][m["qi"]]["answers"]
    i = a_int(a.get("idx"), 0, len(ans) - 1, "Bad answer")
    need(not ans[i]["revealed"], "That answer is already revealed")
    push_undo(m)
    ans[i]["revealed"] = True
    pts, mode = ans[i]["points"], m["rules"]["mode"]
    log("reveal", m, answer=ans[i]["text"], points=pts, phase=m["phase"])
    if m["phase"] == "play":
        if mode == "individual":
            add(m, m["active"], pts, "correct answer")
        else:
            m["pool"] += pts
        fx("reveal", idx=i)
        if all(x["revealed"] for x in ans):
            if mode == "pool":
                add(m, m["active"], m["pool"], "board cleared — pool"); m["pool"] = 0
            m["phase"] = "resolved"
    else:
        s = m["stealer"]
        if mode == "individual":
            add(m, s, pts, "steal")
        else:
            add(m, s, m["pool"], "steal — pool"); m["pool"] = 0
        m["phase"] = "resolved"
        fx("steal_win", idx=i, side=s)
        log("steal", m, result="success", side=s)


@action
def wrong(a):
    m = live(); gate(m)
    need(m["phase"] in ("play", "steal"), "This question is already resolved")
    push_undo(m)
    r = m["rules"]
    if m["phase"] == "play":
        side = m["active"]
        m["strikes"][side] += 1
        n = m["strikes"][side]
        fx("strike", n=n, side=side)
        log("strike", m, side=side, count=n)
        if n >= r["strike_limit"]:
            if r["steal"] and any(not x["revealed"] for x in m["questions"][m["qi"]]["answers"]):
                m["owner"], m["stealer"] = side, other(side)
                m["active"], m["phase"] = other(side), "steal"
                log("steal_start", m, side=other(side))
            else:
                m["phase"] = "resolved"      # pool (if any) is awarded by the host
    else:
        side = m["stealer"]
        fx("strike", n=1, side=side, steal=True)
        log("steal", m, result="failed", side=side)
        if r["mode"] == "pool":
            add(m, m["owner"], m["pool"], "steal failed — pool stays"); m["pool"] = 0
        m["phase"] = "resolved"


@action
def reveal(a):
    m = live(); gate(m)
    need(is_resolved(m), "Answers can be shown after the question is resolved")
    ans = m["questions"][m["qi"]]["answers"]
    i = a_int(a.get("idx"), 0, len(ans) - 1, "Bad answer")
    need(not ans[i]["revealed"], "Already revealed")
    push_undo(m); ans[i]["revealed"] = True
    fx("show", idx=i); log("show", m, answer=ans[i]["text"], points=0)


@action
def reveal_all(a):
    m = live(); gate(m)
    need(is_resolved(m), "Answers can be shown after the question is resolved")
    push_undo(m)
    for x in m["questions"][m["qi"]]["answers"]:
        x["revealed"] = True
    fx("show_all"); log("show_all", m)


@action
def award_pool(a):
    m = live(); gate(m)
    need(is_resolved(m) and m["pool"] > 0, "No pool waiting to be awarded")
    side = a.get("side"); need(side in SIDES, "Bad team")
    push_undo(m); add(m, side, m["pool"], "pool awarded by host"); m["pool"] = 0
    fx("score", side=side)


@action
def adjust(a):
    m = live(); gate(m)
    side = a.get("side"); need(side in SIDES, "Bad team")
    d = a_int(a.get("delta"), -1000, 1000, "Adjustment must be -1000 to 1000")
    need(d != 0, "Adjustment can't be zero")
    push_undo(m); add(m, side, d, "manual adjustment"); fx("score", side=side)


def advance_question(m):
    m["qi"] += 1
    m["strikes"] = {"a": 0, "b": 0}
    m["phase"], m["pool"], m["stealer"], m["owner"] = "play", 0, None, None
    m["active"] = "a" if m["qi"] % 2 == 0 else "b"
    fx("question")


@action
def next_question(a):
    m = live(); gate(m)
    need(is_resolved(m), "Resolve this question first")
    need(m["pool"] == 0, "Award the pool to a team first")
    need(m["qi"] < len(m["questions"]) - 1, "That was the last question — finish the match")
    push_undo(m); advance_question(m); log("next_question", m, question=m["qi"] + 1)


@action
def tiebreak(a):
    m = live(); gate(m)
    need(is_resolved(m) and m["pool"] == 0 and m["qi"] == len(m["questions"]) - 1, "Finish the last question first")
    need(m["scores"]["a"] == m["scores"]["b"], "Scores aren't tied")
    used = {q["id"] for x in T["matches"] if x.get("questions") for q in x["questions"]}
    pick = next((q for q in Q if q.get("enabled", True) and q["answers"] and q["id"] not in used), None)
    need(pick, "No unused enabled question left — add a question first")
    push_undo(m)
    m["questions"].append({"id": pick["id"], "tiebreak": True, "text": pick["text"],
                           "answers": [{"text": x["text"], "points": x["points"], "revealed": False} for x in pick["answers"]]})
    advance_question(m); log("tiebreak", m, question=pick["id"])


@action
def finish(a):
    m = live(); gate(m)
    need(is_resolved(m) and m["pool"] == 0, "Resolve the question and award the pool first")
    need(m["qi"] == len(m["questions"]) - 1, "Play all questions first")
    sa, sb = m["scores"]["a"], m["scores"]["b"]
    need(sa != sb, "Tied — start a tie-break question")
    side = "a" if sa > sb else "b"
    wid = m["teams"][SIDES.index(side)]
    m.update(status="done", winner=wid, winner_side=side, finished=now(), final=dict(m["scores"]))
    if m["id"] == "m1": T["matches"][2]["teams"][0] = wid
    elif m["id"] == "m2": T["matches"][2]["teams"][1] = wid
    else:
        T["champion"], T["completed"] = wid, now()
        log("tournament_complete", m, champion=wid)
    fx("win", team=wid)
    log("match_complete", m, winner=wid, final=m["final"])
    PENDING_BACKUPS.append("done-" + m["id"])


@action
def undo(a):
    m = live()
    need(m["undo"], "Nothing to undo")
    snap = m["undo"].pop()
    for k in GAME_KEYS:
        m[k] = snap[k]
    fx("undo"); log("undo", m)


@action
def team_rename(a):
    t = next((x for x in T["teams"] if x["id"] == a.get("id")), None)
    need(t, "No such team")
    name = str(a.get("name", "")).strip()
    need(1 <= len(name) <= 24, "Team name must be 1–24 characters")
    need(all(m["status"] == "pending" for m in T["matches"] if t["id"] in m["teams"]), "That team's match has already started")
    log("team_rename", None, id=t["id"], old=t["name"], new=name); t["name"] = name


@action
def settings(a):
    s = T["settings"]
    event, rnd = str(a.get("event", "")).strip(), str(a.get("round", "")).strip()
    need(1 <= len(event) <= 40, "Event name must be 1–40 characters")
    need(len(rnd) <= 30, "Round label is too long")
    new = dict(s, event=event, round=rnd)
    rules = {"strike_limit": a_int(a.get("strike_limit"), 1, 5, "Strike limit must be 1–5"),
             "per_match": a_int(a.get("per_match"), 1, 10, "Questions per match must be 1–10"),
             "steal": bool(a.get("steal")), "mode": a.get("mode")}
    need(rules["mode"] in ("pool", "individual"), "Bad scoring mode")
    if any(rules[k] != s[k] for k in rules):
        need(not started_any(), "Scoring rules are locked once a match has started")
    new.update(rules); T["settings"] = new; log("settings", None, **new)


@action
def spotlight(a):
    v = a.get("value"); need(v in ("auto", "title", "bracket"), "Bad screen mode")
    T["spotlight"] = v


@action
def reset(a):
    global T
    need(a.get("confirm") == "RESET", "Type RESET to confirm")
    if (DATA / "tournament.json").exists():
        backup("before-reset")
    log("reset", None)
    old = T["settings"]; T = new_tournament(); T["settings"] = old


@action
def q_save(a):
    q = clean_q(a)
    i = next((k for k, x in enumerate(Q) if x["id"] == q["id"]), None)
    if i is None: Q.append(q)
    else: Q[i] = q
    log("question_save", None, id=q["id"])


@action
def q_delete(a):
    need(any(q["id"] == a.get("id") for q in Q), "No such question")
    Q[:] = [q for q in Q if q["id"] != a.get("id")]
    log("question_delete", None, id=a.get("id"))


@action
def q_import(a):
    items = a.get("questions"); need(isinstance(items, list) and 0 < len(items) <= 500, "Import needs a list of questions")
    cleaned = [clean_q(x) for x in items]
    for q in cleaned:
        i = next((k for k, x in enumerate(Q) if x["id"] == q["id"]), None)
        if i is None: Q.append(q)
        else: Q[i] = q
    log("question_import", None, count=len(cleaned))


def do_action(body):
    global T, Q
    with LOCK:
        t_snap, q_snap, n_audit = copy.deepcopy(T), copy.deepcopy(Q), len(AUDIT)
        try:
            fn = ACTIONS.get(body.get("type"))
            need(fn, "Unknown action")
            aid = body.get("aid")
            need(not (aid and aid in T["seen"]), "Duplicate action ignored")
            fn(body)
            if aid:
                T["seen"].append(aid); del T["seen"][:-300]
            save()
        except BaseException:
            T, Q = t_snap, q_snap
            del AUDIT[n_audit:]
            raise
        while PENDING_BACKUPS:
            try: backup(PENDING_BACKUPS.pop(0))
            except Exception: traceback.print_exc()
    bump()


def bump():
    global VERSION
    with COND:
        VERSION += 1
        COND.notify_all()


# ───────────────────────── views ─────────────────────────
def bracket_view(m):
    d = {"id": m["id"], "label": m["label"], "status": m["status"], "teams": m["teams"],
         "names": [team_name(x) for x in m["teams"]]}
    if m["status"] != "pending":
        d["scores"] = m["scores"]; d["winner"] = team_name(m.get("winner")) if m.get("winner") else None
    return d


def current_view(m, admin):
    q = m["questions"][m["qi"]]
    answers = []
    for i, a in enumerate(q["answers"]):
        r = {"n": i + 1, "revealed": a["revealed"]}
        if a["revealed"] or admin:                       # hidden answers never leave the server for the screen
            r["text"], r["points"] = a["text"], a["points"]
        answers.append(r)
    d = {"id": m["id"], "label": m["label"], "status": m["status"],
         "names": {"a": team_name(m["teams"][0]), "b": team_name(m["teams"][1])},
         "scores": m["scores"], "strikes": m["strikes"], "active": m["active"], "phase": m["phase"],
         "paused": m["paused"], "pool": m["pool"], "stealer": m["stealer"], "owner": m["owner"],
         "qi": m["qi"], "qtotal": len(m["questions"]), "question": q["text"], "tiebreak": q.get("tiebreak", False),
         "answers": answers, "rules": m["rules"], "winner_side": m.get("winner_side"),
         "winner": team_name(m["winner"]) if m.get("winner") else None}
    if admin:
        d["undo"] = len(m["undo"])
        d["tied"] = m["scores"]["a"] == m["scores"]["b"]
    return d


def build(admin=False):
    cur = get_match(T["current"]) if T["current"] else None
    s = {"v": VERSION, "event": T["settings"]["event"], "round": T["settings"]["round"], "status": tstatus(),
         "spotlight": T["spotlight"], "fx": T["fx"], "champion": team_name(T["champion"]) if T["champion"] else None,
         "bracket": [bracket_view(m) for m in T["matches"]],
         "current": current_view(cur, admin) if cur and cur["status"] != "pending" else None}
    if admin:
        s.update(settings=T["settings"], teams=T["teams"], questions=Q, started=started_any(),
                 audit=AUDIT[-60:][::-1])
    return s


# ───────────────────────── http ─────────────────────────
PAGES = {"/admin": ("admin.html", "text/html"), "/screen": ("screen.html", "text/html"),
         "/static/theme.css": ("theme.css", "text/css"), "/static/common.js": ("common.js", "text/javascript"),
         "/static/audio/correct.mp3": ("audio/correct.mp3", "audio/mpeg"),
         "/static/audio/strike.mp3": ("audio/strike.mp3", "audio/mpeg")}


class H(BaseHTTPRequestHandler):
    server_version = "Feud/1.0"

    def log_message(self, *a):
        pass

    def send_body(self, code, body, ctype="application/json", extra=()):
        if not isinstance(body, bytes):
            body = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype + "; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        for k, v in extra:
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def authed(self):
        for p in self.headers.get("Cookie", "").split(";"):
            k, _, v = p.strip().partition("=")
            if k == "feud_admin" and v in TOKENS:
                return True
        return False

    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/":
            return self.send_body(302, b"", "text/plain", [("Location", "/admin")])
        if path in PAGES:
            f, ct = PAGES[path]
            return self.send_body(200, (WEB / f).read_bytes(), ct)
        if path == "/api/screen":
            with LOCK: return self.send_body(200, build(False))
        if path == "/api/admin":
            with LOCK: return self.send_body(200, build(True))
        if path == "/api/health":
            return self.send_body(200, {"ok": True, "status": tstatus()})
        if path == "/events":
            return self.sse()
        self.send_body(404, {"error": "Not found"})

    def sse(self):
        self.send_response(200)
        for k, v in (("Content-Type", "text/event-stream"), ("Cache-Control", "no-cache"), ("Connection", "keep-alive")):
            self.send_header(k, v)
        self.end_headers()
        last = -1
        try:
            while True:
                with COND:
                    if VERSION == last:
                        COND.wait(15)
                    v = VERSION
                self.wfile.write(f"data: {v}\n\n".encode() if v != last else b": ping\n\n")
                self.wfile.flush(); last = v
        except OSError:
            pass

    def body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n > 400_000:
            raise Err("Request too large")
        try:
            d = json.loads(self.rfile.read(n) or b"{}")
        except Exception:
            raise Err("Invalid JSON")
        need(isinstance(d, dict), "Invalid JSON")
        return d

    def do_POST(self):
        path = self.path.split("?")[0]
        try:
            if path == "/api/login":
                ip = self.client_address[0]
                recent = [t for t in FAILS.get(ip, []) if time.time() - t < 60]
                need(len(recent) < 5, "Too many attempts — wait a minute")
                if hmac.compare_digest(str(self.body().get("pin", "")), PIN):
                    tok = secrets.token_urlsafe(24); TOKENS.add(tok)
                    return self.send_body(200, {"ok": True}, extra=[("Set-Cookie", f"feud_admin={tok}; HttpOnly; SameSite=Strict; Path=/")])
                FAILS[ip] = recent + [time.time()]
                return self.send_body(401, {"error": "Wrong PIN"})
            if path == "/api/logout":
                return self.send_body(200, {"ok": True}, extra=[("Set-Cookie", "feud_admin=; Max-Age=0; Path=/")])
            if path == "/api/action":
                do_action(self.body())
                return self.send_body(200, {"ok": True})
            self.send_body(404, {"error": "Not found"})
        except Err as e:
            self.send_body(400, {"error": str(e)})
        except Exception:
            traceback.print_exc()
            self.send_body(500, {"error": "Server error — nothing was changed"})


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def start_server(handler):
    """Bind the requested port, falling back when Windows reserves it."""
    global PORT
    last_error = None
    # A default port can be occupied by Docker/WSL or another local service.
    # Keep an explicitly requested PORT strict so configuration errors remain visible.
    attempts = 20 if "PORT" not in os.environ else 1
    for offset in range(attempts):
        candidate = REQUESTED_PORT + offset
        try:
            server = Server((HOST, candidate), handler)
            PORT = candidate
            if DEBUG:
                print(f"  [debug] bound {HOST}:{PORT}", flush=True)
            return server
        except OSError as exc:
            last_error = exc
            if DEBUG:
                print(f"  [debug] could not bind {HOST}:{candidate}: {exc}", flush=True)
    raise OSError(
        f"Could not bind {HOST}:{REQUESTED_PORT}. "
        "Set PORT to an available port and try again."
    ) from last_error


def lan_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.connect(("8.8.8.8", 80)); ip = s.getsockname()[0]; s.close()
        return ip
    except Exception:
        return "localhost"


if __name__ == "__main__":
    load()
    server = start_server(H)
    print(f"\n  Family Feud server running (state: {tstatus()})")
    if PORT != REQUESTED_PORT:
        print(f"  ! Port {REQUESTED_PORT} is unavailable; using port {PORT}.\n")
    print(f"  Admin   → http://localhost:{PORT}/admin   PIN: {PIN}")
    print(f"  Screen  → http://localhost:{PORT}/screen   (on the network: http://{lan_ip()}:{PORT}/screen)\n")
    if PIN == "feud2026":
        print("  ! Using the default PIN — set ADMIN_PIN before the event.\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("bye")
