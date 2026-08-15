"""Local dashboard for the FriendQuiz flooder.

Serves a browser UI on localhost and runs the flooding engine in the
background, exposing live stats, score targeting and pause/resume via a
small JSON API. All heavy lifting (answer submission, leaderboard reads)
is done server-side so the browser never talks to FriendQuiz directly.
"""

from __future__ import annotations

import itertools
import logging
import os
import random
import threading
import time
import webbrowser
from collections import deque
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import requests
from flask import Flask, jsonify, request, send_from_directory

from config import CONFIG_PATH, DEFAULTS, ENV_MAP, load_saved_config, save_config, validate_config
from utils import random_name

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

API = "https://friendquiz.me"
QUIZ_URL = f"{API}/api/quiz.php"
ANSWER_URL = f"{API}/api/answer.php"
BOARD_URL = f"{API}/api/board.php"

PORT = int(os.environ.get("PORT", "5000"))
HOST = os.environ.get("HOST", "127.0.0.1")
API_TOKEN = os.environ.get("API_TOKEN")
AUTO_OPEN = os.environ.get("AUTO_OPEN", "1").strip().lower() not in {"0", "false", "no"}

app = Flask(__name__, static_folder="static", static_url_path="/static")


@app.before_request
def enforce_api_auth():
    if not API_TOKEN:
        return None
    if not request.path.startswith("/api/"):
        return None
    auth = request.headers.get("Authorization", "")
    token = auth.replace("Bearer ", "", 1).strip() if auth.lower().startswith("bearer ") else ""
    if token == API_TOKEN:
        return None
    return jsonify({"ok": False, "error": "unauthorized"}), 401


def _quiz_code(value: str | None) -> str:
    """Pull the quiz code out of either a bare code or a full quiz URL."""
    value = (value or "").strip()
    if "://" in value or value.startswith("//"):
        path = urlparse(value).path
        parts = [p for p in path.split("/") if p]
        if parts:
            return parts[-1]
    return value


class Engine:
    """Runs the flooding workers and keeps track of their progress."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.config = validate_config({**DEFAULTS, **load_saved_config()})
        self._apply_env_overrides()
        self.quiz: Optional[Dict[str, Any]] = None
        self.running = False
        self.paused = False
        self._stop = threading.Event()
        self._pause = threading.Event()
        self._pause.set()
        self.workers: List[threading.Thread] = []
        self.sessions: List[requests.Session] = []
        self._watcher: Optional[threading.Thread] = None
        self.counter = itertools.count(1)
        self.stats: Optional[Dict[str, Any]] = None
        self.log: deque = deque(maxlen=400)
        self.rate_ts: deque = deque(maxlen=2000)
        self.board: Optional[Dict[str, Any]] = None
        self.board_code: Optional[str] = None
        self.board_at = 0.0

    def _apply_env_overrides(self) -> None:
        """Apply environment variable overrides on top of saved settings."""
        for env_name, key in ENV_MAP.items():
            if env_name not in os.environ:
                continue
            value = os.environ[env_name]
            current = self.config.get(key)
            if isinstance(current, bool):
                self.config[key] = value.strip().lower() in {"1", "true", "yes", "on"}
            elif isinstance(current, int):
                try:
                    self.config[key] = int(value)
                except ValueError:
                    pass
            else:
                self.config[key] = value
        self.config = validate_config(self.config)

    def _log(self, kind: str, text: str) -> None:
        """Append a line to the activity feed shown in the dashboard."""
        self.log.append({"t": time.strftime("%H:%M:%S"), "kind": kind, "text": text})

    def load_quiz(self, code: str) -> Dict[str, Any]:
        """Fetch the quiz definition and cache it for later payloads."""
        code = _quiz_code(code)
        response = requests.get(QUIZ_URL, params={"code": code}, timeout=15)
        response.raise_for_status()
        quiz = response.json()
        if not quiz.get("questions"):
            raise ValueError(f"no questions found for code '{code}'")
        self.quiz = quiz
        return quiz

    def _pick_score(self) -> int:
        """Choose the target score for one submission based on the mode."""
        if self.quiz is None:
            raise RuntimeError("quiz is not loaded")
        question_count = len(self.quiz.get("questions") or [])
        mode = self.config["score_mode"]

        if mode == "perfect":
            return question_count
        if mode == "exact":
            return max(0, min(question_count, int(self.config.get("exact_score", question_count))))
        if mode == "range":
            lo = max(0, min(question_count, int(self.config.get("min_score", 0))))
            hi = max(0, min(question_count, int(self.config.get("max_score", question_count))))
            if hi < lo:
                lo, hi = hi, lo
            return random.randint(lo, hi)
        return random.randint(0, question_count)

    def _name(self) -> str:
        """Generate a name according to the configured name strategy."""
        cfg = self.config
        counter = next(self.counter)
        return random_name(
            prefix=cfg["prefix"],
            suffix=cfg["suffix"],
            letters=cfg.get("letters", True),
            digits=cfg.get("digits", True),
            min_len=cfg.get("min_len", 3),
            max_len=cfg.get("max_len", 8),
            name_mode=cfg["name_mode"],
            counter=counter,
            names_list=cfg.get("names_list", ""),
        )

    def _payload(self, name: str) -> Dict[str, Any]:
        """Build the form payload, hitting the requested score exactly."""
        if self.quiz is None:
            raise RuntimeError("quiz is not loaded")

        questions = self.quiz["questions"]
        values = self.quiz.get("values") or []
        question_count = len(questions)
        score = self._pick_score()
        correct = set(random.sample(range(question_count), score)) if values else set()
        data: Dict[str, Any] = {"code": self.quiz["code"], "name": name}
        for index, question in enumerate(questions):
            options = question["options"]
            if values and index < len(values) and index in correct:
                data[f"q{index + 1}"] = values[index]
            else:
                pool = [option for option in options]
                if values and index < len(values):
                    pool = [option for option in options if option["option_id"] != values[index]] or pool
                data[f"q{index + 1}"] = random.choice(pool)["option_id"]
        return data

    def _worker(self, share: int) -> None:
        """Submit answers until this worker's share is done (-1 = unlimited)."""
        session = requests.Session()
        with self.lock:
            self.sessions.append(session)
        delay = float(self.config.get("delay_ms", 0)) / 1000.0
        sent = 0
        if self.quiz is None:
            return
        question_count = len(self.quiz["questions"])
        while share < 0 or sent < share:
            if self._stop.is_set():
                return
            self._pause.wait(0.2)
            if self._stop.is_set():
                return
            name = self._name()
            try:
                response = session.post(ANSWER_URL, data=self._payload(name), timeout=20)
                response.raise_for_status()
                score = int(response.json().get("score", 0))
                with self.lock:
                    if self.stats is None:
                        return
                    self.stats["ok"] += 1
                    self.stats["score_sum"] += score
                    self.stats["scores"][score] += 1
                    self.rate_ts.append(time.time())
                    self.stats["last"] = {"name": name, "score": score}
                self._log("ok", f"{name}  →  {score}/{question_count}")
            except Exception as exc:  # noqa: BLE001
                if not self._stop.is_set():
                    with self.lock:
                        if self.stats is not None:
                            self.stats["fail"] += 1
                    self._log("err", f"{name}  →  {type(exc).__name__}: {exc}")
            sent += 1
            if delay:
                time.sleep(delay)

    def _watch(self) -> None:
        """Auto-stop once every worker has finished its share."""
        for worker in self.workers:
            worker.join()
        if not self._stop.is_set() and self.running:
            self._log("sys", "all workers finished, auto-stopping")
            self.stop()

    def start(self, cfg: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        """Begin flooding with the given settings, spawning worker threads."""
        if self.running:
            return {"ok": False, "error": "already running"}

        config_source = dict(self.config)
        if cfg:
            config_source.update(cfg)
        try:
            validated = validate_config(config_source)
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}

        code = _quiz_code(validated.get("code") or self.config["code"])
        if not code:
            return {"ok": False, "error": "quiz code is required"}
        if not self.quiz or self.quiz.get("code") != code:
            self.load_quiz(code)

        self.config = validated
        self.config["code"] = code
        save_config(self.config)
        self._stop.clear()
        self._pause.set()
        self.paused = False

        total = max(0, int(self.config.get("count", 0)))
        n_threads = max(1, min(64, int(self.config.get("threads", 8))))
        question_count = len(self.quiz["questions"])
        self.stats = {
            "total": total,
            "ok": 0,
            "fail": 0,
            "score_sum": 0,
            "scores": [0] * (question_count + 1),
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
        for worker in self.workers:
            worker.start()
        self._watcher = threading.Thread(target=self._watch, daemon=True)
        self._watcher.start()
        self._log("sys", f"started: {n_threads} workers → {total if total else 'unlimited'}")
        return {"ok": True}

    def stop(self) -> Dict[str, Any]:
        """Signal all workers to stop and wait for them to exit."""
        if not self.running:
            return {"ok": True}
        self._stop.set()
        with self.lock:
            sessions, self.sessions = self.sessions, []
        for session in sessions:
            try:
                session.close()
            except Exception:  # noqa: BLE001
                pass
        deadline = time.time() + 3
        for worker in self.workers:
            remaining = deadline - time.time()
            if remaining <= 0:
                break
            worker.join(timeout=remaining)
        self.running = False
        self.paused = False
        self._log("sys", "stopped")
        return {"ok": True}

    def pause(self) -> Dict[str, Any]:
        """Freeze all workers in place (they block until resumed)."""
        if not self.running:
            return {"ok": False, "error": "not running"}
        self.paused = True
        self._pause.clear()
        self._log("sys", "paused")
        return {"ok": True}

    def resume(self) -> Dict[str, Any]:
        if not self.running:
            return {"ok": False, "error": "not running"}
        self.paused = False
        self._pause.set()
        self._log("sys", "resumed")
        return {"ok": True}

    def status(self) -> Dict[str, Any]:
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
        rate = 0.0
        if self.rate_ts:
            cutoff = now - 10
            recent = [timestamp for timestamp in self.rate_ts if timestamp >= cutoff]
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

    def board_data(self, code: str) -> Dict[str, Any]:
        """Read the live leaderboard, caching it for a few seconds."""
        if self.board and self.board_code == code and time.time() - self.board_at < 3:
            return self.board
        try:
            response = requests.get(BOARD_URL, params={"code": code}, timeout=15)
            response.raise_for_status()
            self.board = response.json()
            self.board_code = code
            self.board_at = time.time()
            return self.board
        except Exception:  # noqa: BLE001
            return {"error": "board unavailable"}


engine = Engine()


@app.get("/")
def index():
    return send_from_directory(app.static_folder, "dashboard.html")


@app.get("/healthz")
def healthz():
    return jsonify({"ok": True, "status": "healthy", "running": engine.running})


@app.get("/readyz")
def readyz():
    return jsonify({"ok": True, "ready": True, "quiz_loaded": engine.quiz is not None})


@app.get("/api/config")
def get_config():
    return jsonify({"config": engine.config})


@app.post("/api/config")
def post_config():
    data = request.get_json(force=True, silent=True) or {}
    try:
        engine.config = validate_config({**engine.config, **data})
        save_config(engine.config)
        return jsonify({"config": engine.config})
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400


@app.post("/api/load_quiz")
def load_quiz_route():
    data = request.get_json(force=True, silent=True) or {}
    code = (data.get("code") or engine.config["code"]).strip()
    try:
        quiz = engine.load_quiz(code)
        return jsonify({
            "ok": True,
            "quiz": {
                "title": quiz.get("title"),
                "name": quiz.get("name"),
                "code": quiz.get("code"),
                "questions": len(quiz.get("questions") or []),
                "values": bool(quiz.get("values")),
            },
        })
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(exc)}), 400


@app.post("/api/start")
def start_route():
    data = request.get_json(force=True, silent=True) or {}
    try:
        return jsonify(engine.start(data))
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(exc)}), 400


@app.post("/api/stop")
def stop_route():
    return jsonify(engine.stop())


@app.post("/api/pause")
def pause_route():
    return jsonify(engine.pause())


@app.post("/api/resume")
def resume_route():
    return jsonify(engine.resume())


@app.get("/api/status")
def status_route():
    return jsonify(engine.status())


@app.get("/api/board")
def board_route():
    code = _quiz_code(request.args.get("code") or engine.config["code"])
    return jsonify(engine.board_data(code))


if __name__ == "__main__":
    print(f"[*] FriendQuiz Flooder dashboard running at http://{HOST}:{PORT}")
    if HOST in ("127.0.0.1", "localhost") and AUTO_OPEN:
        threading.Timer(1.0, lambda: webbrowser.open(f"http://127.0.0.1:{PORT}")).start()
    app.run(host=HOST, port=PORT, debug=False, threaded=True)