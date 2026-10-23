# LibMS

A Flask + SQLite library management system with hand-written data structures for caching, autocomplete and recommendations.

[![CI](https://github.com/vishnuatgit/LibMS/actions/workflows/ci.yml/badge.svg)](https://github.com/vishnuatgit/LibMS/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/python-3.11%20%7C%203.12-blue)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

## What it does

Members sign up, browse and search a seeded catalogue of about 60 titles, borrow and return books, renew once, place holds on titles with no free copy, review books and suggest new acquisitions. Overdue loans accrue a fine of Rs. 5 per day. An admin panel covers active loans (with force return), book add/edit/delete, member ban/unban and notifications, acquisition suggestions and simple circulation reports.

## Custom data structures (`dsa.py`)

| Structure | Used for | Complexity |
|---|---|---|
| LRU cache (hash map + doubly linked list) | Caches book-detail records by id (capacity 200) | `get`/`put` O(1) |
| Trie over every word-suffix of each title | `/api/autocomplete` on the catalogue search box | lookup O(P) for a prefix of length P; each node keeps up to 6 matches |
| Weighted adjacency-list graph + BFS (depth 2) | "Related books" on the book page; edges join books that share a tag (weight 1) or an author (weight 2) | O(V + E) per query |

Holds are queued in the database and served by `priority_level`, then placement time. Every member currently gets the default priority, so in practice the queue is first come, first served.

## Tech stack

Python, Flask, SQLite (WAL mode), bcrypt, Flask-Limiter, Jinja2 templates with plain CSS/JS, gunicorn, Docker, GitHub Actions.

## Quick start (local)

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env             # FLASK_DEBUG=1 for local development
flask --app app run              # http://127.0.0.1:5000
```

The database is created and the catalogue seeded on first start. A default admin account is created: `admin` / `Admin@123`. Change it before exposing the app anywhere.

## Quick start (Docker)

```bash
cp .env.example .env    # set SECRET_KEY to a long random value
docker compose up --build    # http://localhost:8000
```

The image runs gunicorn as a non-root user with a health check; the SQLite file lives in the `libms-data` volume.

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `SECRET_KEY` | none | Signs session cookies. Required unless `FLASK_DEBUG=1` (then a random key is generated per run). |
| `FLASK_DEBUG` | unset | `1` enables debug mode for local development. |
| `DATABASE_PATH` | `library.db` | SQLite file location. |
| `SESSION_COOKIE_SECURE` | `0` | Set to `1` when served over HTTPS. |

## Running tests

```bash
pip install -r requirements-dev.txt
SECRET_KEY=test python -m pytest --cov=app --cov=dsa
```

Route tests use Flask's test client against a temporary SQLite database.

## Security notes

bcrypt password hashing, CSRF tokens on all POST forms, parameterised SQL, rate-limited login and signup, HTTP-only session cookies. Known gap: borrow, return, renew, hold and several admin actions are still plain GET links; moving them to POST forms is on the roadmap.

## Deployment

`render.yaml` describes a Render web service (generated `SECRET_KEY`, gunicorn start command) and `Procfile` covers Heroku-style hosts. On free tiers the instance sleeps when idle and the SQLite file is reset on redeploy; the app re-creates and re-seeds the catalogue on start.

## Roadmap

- Convert state-changing GET routes to POST with CSRF
- Split `app.py` into blueprints and add a lint step
- Admin action to record fine payments

## License

MIT, see [LICENSE](LICENSE).
