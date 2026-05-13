import sqlite3

db = sqlite3.connect("library.db")
try:
    db.execute("ALTER TABLE users ADD COLUMN priority_level INTEGER DEFAULT 1")
except sqlite3.OperationalError as e:
    print(f"Column might exist: {e}")

db.execute("""
CREATE TABLE IF NOT EXISTS search_queries (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER REFERENCES users(id) ON DELETE SET NULL,
    query      TEXT NOT NULL,
    created_at TEXT DEFAULT (datetime('now'))
);
""")

db.execute("""
CREATE TABLE IF NOT EXISTS book_similarities (
    book1_id   INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    book2_id   INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    score      INTEGER NOT NULL,
    PRIMARY KEY(book1_id, book2_id)
);
""")
db.commit()
db.close()
print("Migration completed.")
