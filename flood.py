"""Command-line flooder for FriendQuiz leaderboards.

Submits anonymous answers to a FriendQuiz quiz in bulk, either with
random options or with the correct answers the API exposes.
"""

from __future__ import annotations

import argparse
import random
import sys
import threading
import time
from typing import Any, Dict

import requests

from config import validate_config
from utils import random_name

API = "https://friendquiz.me"
SUBMIT_ENDPOINT = f"{API}/api/answer.php"
QUIZ_ENDPOINT = f"{API}/api/quiz.php"


def load_quiz(code: str) -> Dict[str, Any]:
    """Fetch the quiz definition for a given code."""
    response = requests.get(QUIZ_ENDPOINT, params={"code": code}, timeout=15)
    response.raise_for_status()
    quiz = response.json()
    if not quiz.get("questions"):
        raise ValueError(f"no questions found for code '{code}'")
    return quiz


def build_payload(code: str, quiz: Dict[str, Any], name: str, mode: str) -> Dict[str, Any]:
    """Build the form-encoded answer payload for one submission."""
    values = quiz.get("values") or []
    questions = quiz.get("questions") or []
    data: Dict[str, Any] = {"code": code, "name": name}
    for index, question in enumerate(questions):
        if mode == "perfect" and index < len(values):
            data[f"q{index + 1}"] = values[index]
        else:
            data[f"q{index + 1}"] = random.choice(question["options"])["option_id"]
    return data


def submit(session: requests.Session, quiz: Dict[str, Any], code: str, name: str, mode: str, stats: Dict[str, Any]) -> None:
    """Send a single submission and record the outcome."""
    try:
        payload = build_payload(code, quiz, name, mode)
        response = session.post(SUBMIT_ENDPOINT, data=payload, timeout=20)
        response.raise_for_status()
        body = response.json()
        with stats["lock"]:
            stats["ok"] += 1
            stats["score"] += int(body.get("score", 0))
            stats["last"] = (name, int(body.get("score", 0)), response.status_code)
    except Exception as exc:  # noqa: BLE001
        with stats["lock"]:
            stats["fail"] += 1
            stats["last_err"] = f"{name}: {exc!r}"


def worker(quiz: Dict[str, Any], code: str, mode: str, count: int, stats: Dict[str, Any], delay: float, stop: threading.Event) -> None:
    """Run submissions in a loop until the worker's share is done."""
    session = requests.Session()
    for _ in range(count):
        if stop.is_set():
            break
        submit(session, quiz, code, random_name(), mode, stats)
        if delay > 0:
            time.sleep(delay)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Flood a FriendQuiz leaderboard with fake answers.")
    parser.add_argument("code", help="quiz code, e.g. U4aV9HED")
    parser.add_argument("-n", "--count", type=int, default=100, help="total submissions (default 100)")
    parser.add_argument("-t", "--threads", type=int, default=8, help="concurrent workers (default 8)")
    parser.add_argument("-d", "--delay", type=float, default=0.0, help="seconds between requests per worker")
    parser.add_argument(
        "-m",
        "--mode",
        choices=["random", "perfect"],
        default="random",
        help="'random' picks random options, 'perfect' always gets the correct answer on each question",
    )
    parser.add_argument("--quiet", action="store_true", help="only print the final summary")
    return parser.parse_args()


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass

    args = parse_args()
    cfg = validate_config({
        "count": args.count,
        "threads": args.threads,
        "delay_ms": int(args.delay * 1000),
        "score_mode": args.mode,
        "name_mode": "random",
        "letters": True,
        "digits": True,
    })
    if cfg["count"] <= 0:
        raise ValueError("count must be greater than 0")
    if cfg["threads"] <= 0:
        raise ValueError("threads must be greater than 0")

    print(f"[*] Loading quiz {args.code} ...")
    quiz = load_quiz(args.code)
    print(f"[*] Quiz: {quiz.get('title')} — {len(quiz.get('questions') or [])} questions")

    stats: Dict[str, Any] = {"ok": 0, "fail": 0, "score": 0, "lock": threading.Lock(), "last": None, "last_err": None}
    stop = threading.Event()
    per_worker = -(-cfg["count"] // cfg["threads"])
    threads = [
        threading.Thread(
            target=worker,
            args=(quiz, args.code, args.mode, per_worker, stats, args.delay, stop),
            daemon=True,
        )
        for _ in range(cfg["threads"])
    ]

    if not args.quiet:
        print(f"[*] Firing {cfg['count']} submissions across {cfg['threads']} workers (mode={args.mode}) ...")
    started = time.time()
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    elapsed = time.time() - started
    print(f"[+] done: {stats['ok']} ok / {stats['fail']} failed in {elapsed:.1f}s")
    if stats["ok"]:
        print(f"[+] avg score: {stats['score'] / stats['ok']:.1f}  |  last entry: {stats['last']}")
    if stats["fail"]:
        print(f"[!] sample error: {stats['last_err']}")


if __name__ == "__main__":
    main()
