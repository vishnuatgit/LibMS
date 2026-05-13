# LibMS: Advanced Library Management System

A high-performance, production-grade Library Management System built with Flask and SQLite. Designed as a comprehensive technical showcase, this project integrates custom Data Structures and Algorithms (DSA) from scratch, robust automated testing, and a fully containerized deployment pipeline.

## 🚀 Technical Highlights

- **Custom LRU Cache:** Built a custom $O(1)$ Hash Map + Doubly Linked List caching layer for the catalogue, drastically reducing database loads for popular books.
- **Prefix Trie Autocomplete:** Implemented an in-memory Prefix Trie to power an instant $O(L)$ search dropdown on the frontend without relying on heavy SQL `LIKE` queries.
- **Graph-Based Recommendations:** A custom Adjacency List graph uses Breadth-First Search (BFS) to traverse book metadata (tags, authors) up to 2 degrees of separation to provide highly accurate book recommendations.
- **Priority Queue System:** The reservation system operates as a database-backed priority queue, ensuring high-priority users dynamically jump the queue.
- **Robust Architecture:** Fully Dockerized, comprehensively tested via `pytest`, and governed by a strict GitHub Actions CI/CD pipeline.

## ✨ Core Features

- **Auth** — Signup, login, bcrypt password hashing, and user role management.
- **Books** — Browse, search, filter, and sort the entire catalogue.
- **Issue/Return** — Robust 14-day tracking, renewals, and automated ₹5/day fine calculations.
- **Reservations** — Queue system for unavailable books that auto-notifies upon return.
- **Admin Panel** — Comprehensive oversight to manage books, users, returns, and system health.
- **Activity Log** — Full audit trail of user actions.

## ⚙️ Quickstart (Docker)

The fastest way to run this locally is using Docker Compose.

```bash
docker-compose up --build
```
The app will be running at `http://localhost:5000`.

## ⚙️ Quickstart (Local)

```bash
# 1. Setup Virtual Environment
python3 -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# 2. Install dependencies
pip install -r requirements.txt

# 3. Run
python app.py
```

## 🔒 Default Admin Credentials

| Username | Password  |
|----------|-----------|
| admin    | Admin@123  |

## 🧪 Testing

The repository contains a dedicated `tests/` suite covering core DSA logic and Flask routes.

```bash
python -m pytest tests/ -v
```
