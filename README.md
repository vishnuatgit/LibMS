# LibMS (Library Management System)

A full-stack library management system I built using Python, Flask, and SQLite. The primary goal of this project was to go beyond standard CRUD applications by integrating custom Data Structures and Algorithms (DSA) from scratch to handle real-world system optimization like caching, fast searching, and recommendation engines.

## Technical Deep Dive (Custom DSA)

Instead of relying solely on SQL queries or heavy external dependencies, I implemented the following custom features to optimize the application's performance:

- **Custom LRU Cache:** 
  I built a Hash Map combined with a Doubly Linked List to serve as a caching layer for the catalogue. When users browse frequently accessed books, the data is served from memory in $O(1)$ time, drastically reducing the number of database queries.

- **Prefix Trie Autocomplete:** 
  To make the search bar instantly responsive, I implemented an in-memory Prefix Trie. Instead of running expensive SQL `LIKE` queries against the database on every keystroke, the Prefix Trie handles autocomplete suggestions on the backend with $O(L)$ time complexity.

- **Graph-Based Recommendations (BFS):** 
  The recommendation system uses a custom Adjacency List graph. It performs a Breadth-First Search (BFS) to traverse book metadata (like matching tags and authors) up to two degrees of separation. This provides users with highly accurate, contextual book suggestions based on what they are currently viewing.

- **Priority Queue Waitlist System:** 
  The book reservation system operates as a database-backed priority queue. When a book is checked out, users can join the waitlist. High-priority users can dynamically jump ahead in the queue, ensuring the system handles reservations intelligently based on user roles.

## Core Features

- **Authentication & Authorization:** Secure user signup and login with bcrypt password hashing. Differentiates between standard users and system administrators.
- **Library Catalog Operations:** Users can browse, search, filter, and sort the entire book catalogue seamlessly.
- **Issue and Return Management:** Robust tracking of book checkouts with a strict 14-day limit. The system automatically handles renewal requests and calculates late fines (₹5/day).
- **Automated Notifications:** When a reserved book is returned to the library, the system automatically alerts the next person in the priority queue.
- **Admin Dashboard:** A comprehensive oversight panel for administrators to manage inventory, track active issues, monitor users, and view system health metrics.

## Running the Project Locally

The fastest way to spin up the application and its environment is using Docker.

```bash
docker-compose up --build
```
The application will be accessible at `http://localhost:5000`.

### Manual Setup (Without Docker)

If you prefer to run the application directly using Python:

```bash
# 1. Setup Virtual Environment
python -m venv venv

# On Windows:
venv\Scripts\activate
# On macOS/Linux:
source venv/bin/activate

# 2. Install dependencies
pip install -r  requirements.txt

# 3. Run the server
python app.py
```

### Default Admin Credentials

If you need administrator access to test the dashboard, you can use these default credentials:
- **Username:** admin
- **Password:** Admin@123

## Testing

The repository contains a dedicated test suite that covers the core DSA logic as well as the Flask application routes. The tests are written using `pytest`.

To run the test suite:
```bash
python -m pytest tests/ -v
```
