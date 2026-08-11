<p align="center">
  <img src="https://img.shields.io/badge/Python-3.11-blue?style=for-the-badge&logo=python&logoColor=white">
  <img src="https://img.shields.io/badge/Flask-3.0-lightgrey?style=for-the-badge&logo=flask&logoColor=white">
  <img src="https://img.shields.io/badge/Docker-Ready-2496ED?style=for-the-badge&logo=docker&logoColor=white">
  <img src="https://img.shields.io/badge/License-MIT-green?style=for-the-badge">
</p>

# FriendQuiz Flooder

**FriendQuiz Flooder** is an automation tool for [FriendQuiz](https://friendquiz.me) that submits bulk answers to any public quiz leaderboard. It works with both the command line and a sleek real-time web dashboard, and it can target **exact scores**, run on **up to 64 concurrent threads**, and be paused, resumed or stopped at any moment.

> 🎯 Perfect scores, realistic random names, live leaderboard tracking — all from your browser.

---

## ✨ Features

- ⚡ **High-throughput flooding** — up to 64 parallel workers with configurable delay between submissions
- 🎯 **Exact score targeting** — hit a fixed score (e.g. always 8/10) or a random score in any range
- 🧠 **Perfect mode** — answers every question correctly by reading the answers the quiz API returns in plain text
- 🏷️ **Flexible name generation** — random, numbered, or your own custom list, with prefix/suffix and charset controls
- 🖥️ **Live dashboard** — throughput (req/s), progress bar, score histogram, activity log and a live leaderboard panel
- ⏯️ **Pause / resume / stop** — full control mid-run, or leave it on unlimited mode
- 🐳 **Docker-ready** — one command to build and run
- 💾 **Persistent settings** — your quiz code and options auto-save between sessions

---

## 🚀 Quick Start

### Requirements

- Python 3.11+
- `pip install -r requirements.txt`

### Web dashboard (recommended)

```bash
python server.py
```

Your browser opens automatically at **http://127.0.0.1:5000**. Enter a quiz code, tune the settings, hit **Start**.

### Command line

```bash
python flood.py U4aV9HED -n 500 -t 16 -m perfect
```

| Option | Description | Default |
| ------ | ----------- | ------- |
| `code` | The quiz code from the quiz URL | required |
| `-n, --count` | Total submissions to send | 100 |
| `-t, --threads` | Concurrent worker threads | 8 |
| `-d, --delay` | Seconds to wait between requests per worker | 0 |
| `-m, --mode` | `random` or `perfect` | random |
| `--quiet` | Suppress per-batch output | — |

---

## 🐳 Docker

```bash
docker compose up -d --build
```

Then open **http://localhost:5000** (or **http://localhost:18080** via the compose port mapping). Dashboard UI and code ship inside the image, and your settings persist across restarts in the `flooder-config` named volume.

---

## ☁️ Deploy with Coolify

Every app setting can be overridden with an environment variable, and the whole config is editable straight from the **Coolify → Environments** tab.

1. In Coolify, add a new resource from your Git repository.
2. Choose **Docker Compose** as the build pack (the repo ships a `docker-compose.yml`).
3. Under **Environment Variables**, tweak any of the values below and hit deploy/restart.

| Variable | Setting it controls | Default |
| -------- | ------------------- | ------- |
| `QUIZ_CODE` | Quiz code loaded on startup | _(empty)_ |
| `COUNT` | Total submissions (`0` = unlimited) | `100` |
| `THREADS` | Concurrent workers (1–64) | `8` |
| `DELAY_MS` | Pause between requests per worker | `0` |
| `SCORE_MODE` | `perfect`, `exact`, `range` or `random` | `random` |
| `EXACT_SCORE` | Target score for `exact` mode | `10` |
| `MIN_SCORE` / `MAX_SCORE` | Range for `range` mode | `0` / `10` |
| `NAME_MODE` | `random`, `counter` or `list` | `random` |
| `NAME_PREFIX` / `NAME_SUFFIX` | Wraps generated names | _(empty)_ |
| `MIN_LEN` / `MAX_LEN` | Random-name length bounds | `3` / `8` |
| `NAME_LETTERS` / `NAME_DIGITS` | Charset toggles (`true`/`false`) | `true` |
| `NAMES_LIST` | Comma/newline names for `list` mode | _(empty)_ |

### Changing the port

The app listens on **port 5000 inside the container**, which is fixed, and is exposed on host port **18080** by default (a high, rarely-used port to avoid conflicts). To change it, edit the mapping in Coolify's **Ports Mappings** section (or the `ports:` entry in the compose file) — env vars do **not** control the published port. If you hit `Bind for :::5000 failed: port is already allocated`, that means the container port 5000 is held by an old container on your server — delete it and redeploy, or pick a different container port in the mapping.

### Env vars appear locked in the UI?

Some Coolify versions treat variables that come from the compose file as **locked** (view-only). The workaround: delete the entry from the compose file's `environment:` block (or your Coolify copy of it), then add the variable manually in Coolify's **Environment Variables** tab — manually added vars are fully editable and still reach the container. Alternatively, everything here can be changed from the dashboard's own UI, which saves your settings to the `flooder-config` volume (so it persists across redeploys).

Environment variables take precedence over `config.json` and the dashboard defaults, so they always win on restart. A `.env.example` template is included for local development.

---

## 🎛️ Dashboard Options

| Setting | What it does |
| ------- | ------------ |
| **Total submissions** | How many answers to send (use `∞` for unlimited) |
| **Concurrency** | Number of parallel workers (1–64) |
| **Delay** | Pause between requests per worker (ms) |
| **Score mode** | Perfect / Exact / Range / Random |
| **Name mode** | Random / Counter / List, with prefix, suffix and length controls |

The live panel shows average score, requests per second, remaining count, ETA and a score histogram — plus the quiz's real leaderboard updating in real time.

---

## 🧠 How It Works

FriendQuiz exposes an unauthenticated JSON API. The quiz definition is fetched from `/api/quiz.php`, which conveniently returns the **correct answer IDs** in the `values` field. The flooder then submits form-encoded answers (`code`, `name`, `q1..qN`) to `/api/answer.php` — the same request the site's own frontend makes — and reads the resulting score and leaderboard back.

---

## ⚠️ Disclaimer

This project is for **educational purposes only**. Flooding a leaderboard with fake submissions may violate FriendQuiz's terms of service. Use it on quizzes you own, and be respectful of other users' leaderboards. The author is not responsible for any misuse.

---

## 📄 License

[MIT](LICENSE)
