"""
LibMS
A Koha-inspired, GSoC-grade library management system.

Schema highlights:
  - books          : full bibliographic record (ISBN-13, Dewey, language, edition, pages)
  - authors        : normalised author table (many books per author)
  - publishers     : normalised publisher table
  - book_authors   : M:M join
  - tags / book_tags : subject tags, M:M join
  - item_copies    : individual physical copies (barcode, condition, location)
  - members        : extended patron record (student ID, department, membership tier)
  - transactions   : circulation with copy-level tracking
  - holds          : queue-based hold system with position
  - fines          : fine ledger (separate from transactions)
  - acquisitions   : patron book-suggestion workflow
  - reviews        : star ratings + comments
  - notifications  : per-user inbox
  - activity_log   : full audit trail with IP

Security:
  - bcrypt (12 rounds), CSRF tokens, rate limiting
  - bleach sanitisation, parameterised SQL, WAL mode
  - Session regeneration on login, httponly cookies
"""

from flask import (Flask, render_template, redirect, request,
                   flash, session, jsonify, g, abort)
from datetime import datetime, timedelta
from functools import wraps
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
import sqlite3, bcrypt, os, re, bleach, secrets, random
from dotenv import load_dotenv
from dsa import catalogue_cache, search_trie, recommendation_graph
load_dotenv()

app = Flask(__name__)
_sec = os.environ.get("SECRET_KEY") or secrets.token_hex(32)
app.secret_key = _sec
app.config.update(
    SESSION_COOKIE_HTTPONLY   = True,
    SESSION_COOKIE_SAMESITE   = "Lax",
    SESSION_COOKIE_SECURE     = False,
    PERMANENT_SESSION_LIFETIME = timedelta(hours=8),
)

limiter = Limiter(get_remote_address, app=app, default_limits=[], storage_uri="memory://")

DB            = "library.db"
FINE_PER_DAY  = 5
BORROW_DAYS   = 14
MAX_BOOKS     = 5
BCRYPT_ROUNDS = 12
MAX_TEXT      = 1000
USERNAME_RE   = re.compile(r'^[a-zA-Z0-9_]{3,32}$')
EMAIL_RE      = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')
STUDENT_ID_RE = re.compile(r'^[A-Z0-9]{4,20}$')

COVER_COLORS = [
    "#1e3a5f","#2d4a2d","#5c1e1e","#3d2b5c","#1a4a4a",
    "#4a3520","#1e3a1e","#3a1e3a","#1e2d4a","#4a2020",
    "#2d3a4a","#3a4a2d","#4a3a1e","#1e4a3a","#3a2d4a",
]

# ══════════════════════════════════════════════════════════════════
#  DATABASE
# ══════════════════════════════════════════════════════════════════

def get_db():
    if "db" not in g:
        c = sqlite3.connect(DB)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA foreign_keys = ON")
        c.execute("PRAGMA journal_mode = WAL")
        c.execute("PRAGMA busy_timeout = 5000")
        c.execute("PRAGMA cache_size = -8000")
        g.db = c
    return g.db

@app.teardown_appcontext
def close_db(e): db = g.pop("db", None);  db and db.close()

def init_db():
    with app.app_context():
        db = get_db()
        db.executescript("""
        /* ── Core catalogue tables ── */
        CREATE TABLE IF NOT EXISTS publishers (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            name        TEXT UNIQUE NOT NULL,
            country     TEXT,
            website     TEXT
        );

        CREATE TABLE IF NOT EXISTS authors (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            name        TEXT NOT NULL,
            bio         TEXT
        );

        CREATE TABLE IF NOT EXISTS books (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            isbn            TEXT UNIQUE,
            title           TEXT NOT NULL,
            subtitle        TEXT,
            publisher_id    INTEGER REFERENCES publishers(id),
            pub_year        INTEGER,
            edition         TEXT,
            pages           INTEGER,
            language        TEXT DEFAULT 'English',
            category        TEXT NOT NULL,
            dewey           TEXT,
            description     TEXT,
            cover_color     TEXT DEFAULT '#1e3a5f',
            added_at        TEXT DEFAULT (date('now'))
        );

        CREATE TABLE IF NOT EXISTS book_authors (
            book_id    INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
            author_id  INTEGER NOT NULL REFERENCES authors(id) ON DELETE CASCADE,
            role       TEXT DEFAULT 'author',
            PRIMARY KEY (book_id, author_id)
        );

        CREATE TABLE IF NOT EXISTS tags (
            id   INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL COLLATE NOCASE
        );

        CREATE TABLE IF NOT EXISTS book_tags (
            book_id INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
            tag_id  INTEGER NOT NULL REFERENCES tags(id)  ON DELETE CASCADE,
            PRIMARY KEY (book_id, tag_id)
        );

        /* ── Physical copies ── */
        CREATE TABLE IF NOT EXISTS item_copies (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            book_id     INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
            barcode     TEXT UNIQUE NOT NULL,
            condition   TEXT DEFAULT 'Good',   -- Good / Fair / Damaged / Lost
            location    TEXT DEFAULT 'Main Stack',
            is_active   INTEGER DEFAULT 1
        );

        /* ── Members (extended users) ── */
        CREATE TABLE IF NOT EXISTS users (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            member_id       TEXT UNIQUE NOT NULL,
            username        TEXT UNIQUE NOT NULL COLLATE NOCASE,
            email           TEXT UNIQUE COLLATE NOCASE,
            password        BLOB NOT NULL,
            full_name       TEXT,
            phone           TEXT,
            department      TEXT,
            membership_type TEXT DEFAULT 'Student', -- Student / Faculty / Staff
            priority_level  INTEGER DEFAULT 1,
            is_admin        INTEGER DEFAULT 0,
            is_banned       INTEGER DEFAULT 0,
            joined_at       TEXT DEFAULT (date('now')),
            expiry_date     TEXT DEFAULT (date('now','+1 year')),
            last_login      TEXT,
            login_count     INTEGER DEFAULT 0
        );

        /* ── Circulation ── */
        CREATE TABLE IF NOT EXISTS transactions (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            copy_id     INTEGER NOT NULL REFERENCES item_copies(id),
            book_id     INTEGER NOT NULL REFERENCES books(id),
            issued_at   TEXT NOT NULL,
            due_date    TEXT NOT NULL,
            returned_at TEXT,
            renewed     INTEGER DEFAULT 0,
            notes       TEXT
        );

        CREATE TABLE IF NOT EXISTS fines (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            txn_id      INTEGER REFERENCES transactions(id),
            amount      INTEGER NOT NULL,
            reason      TEXT,
            paid        INTEGER DEFAULT 0,
            created_at  TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS holds (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            book_id     INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
            placed_at   TEXT DEFAULT (datetime('now')),
            status      TEXT DEFAULT 'waiting',  -- waiting / ready / fulfilled / cancelled
            notified    INTEGER DEFAULT 0,
            UNIQUE(user_id, book_id)
        );

        /* ── Acquisitions suggestions ── */
        CREATE TABLE IF NOT EXISTS acquisitions (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     INTEGER REFERENCES users(id) ON DELETE SET NULL,
            title       TEXT NOT NULL,
            author      TEXT,
            isbn        TEXT,
            reason      TEXT,
            status      TEXT DEFAULT 'pending',  -- pending / approved / rejected / ordered
            admin_note  TEXT,
            created_at  TEXT DEFAULT (datetime('now'))
        );

        /* ── Reviews ── */
        CREATE TABLE IF NOT EXISTS reviews (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            book_id    INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
            rating     INTEGER CHECK(rating BETWEEN 1 AND 5),
            comment    TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            UNIQUE(user_id, book_id)
        );

        /* ── Notifications ── */
        CREATE TABLE IF NOT EXISTS notifications (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            message    TEXT NOT NULL,
            type       TEXT DEFAULT 'info',
            is_read    INTEGER DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now'))
        );

        /* ── Audit log ── */
        CREATE TABLE IF NOT EXISTS activity_log (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id    INTEGER REFERENCES users(id) ON DELETE SET NULL,
            action     TEXT NOT NULL,
            detail     TEXT,
            ip         TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );

        /* ── DSA Extensions ── */
        CREATE TABLE IF NOT EXISTS search_queries (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id    INTEGER REFERENCES users(id) ON DELETE SET NULL,
            query      TEXT NOT NULL,
            created_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS book_similarities (
            book1_id   INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
            book2_id   INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
            score      INTEGER NOT NULL,
            PRIMARY KEY(book1_id, book2_id)
        );

        /* ── Indexes ── */
        CREATE INDEX IF NOT EXISTS idx_txn_user     ON transactions(user_id);
        CREATE INDEX IF NOT EXISTS idx_txn_book     ON transactions(book_id);
        CREATE INDEX IF NOT EXISTS idx_txn_open     ON transactions(user_id, returned_at);
        CREATE INDEX IF NOT EXISTS idx_copy_book    ON item_copies(book_id);
        CREATE INDEX IF NOT EXISTS idx_holds_user   ON holds(user_id);
        CREATE INDEX IF NOT EXISTS idx_holds_book   ON holds(book_id);
        CREATE INDEX IF NOT EXISTS idx_fines_user   ON fines(user_id, paid);
        CREATE INDEX IF NOT EXISTS idx_notif_user   ON notifications(user_id, is_read);
        CREATE INDEX IF NOT EXISTS idx_review_book  ON reviews(book_id);
        CREATE INDEX IF NOT EXISTS idx_book_cat     ON books(category);
        CREATE INDEX IF NOT EXISTS idx_ba_book      ON book_authors(book_id);
        CREATE INDEX IF NOT EXISTS idx_acq_status   ON acquisitions(status);
        """)
        db.commit()
        _seed_catalogue(db)
        _seed_admin(db)

def _make_barcode(book_id, copy_num):
    return f"LIB{book_id:05d}{copy_num:03d}"

def _seed_catalogue(db):
    if db.execute("SELECT COUNT(*) FROM books").fetchone()[0] > 0:
        return

    # ── publishers ──
    publishers = [
        ("O'Reilly Media", "USA", "https://oreilly.com"),
        ("Pearson Education", "USA", "https://pearson.com"),
        ("MIT Press", "USA", "https://mitpress.mit.edu"),
        ("Prentice Hall", "USA", "https://pearson.com"),
        ("Addison-Wesley", "USA", "https://pearson.com"),
        ("Packt Publishing", "UK", "https://packtpub.com"),
        ("Manning Publications", "USA", "https://manning.com"),
        ("No Starch Press", "USA", "https://nostarch.com"),
        ("McGraw-Hill", "USA", "https://mheducation.com"),
        ("Wiley", "USA", "https://wiley.com"),
        ("HarperCollins", "USA", "https://harpercollins.com"),
        ("Penguin Books", "UK", "https://penguin.co.uk"),
        ("Bloomsbury", "UK", "https://bloomsbury.com"),
        ("Crown Business", "USA", "https://crownpublishing.com"),
        ("Harriman House", "UK", "https://harriman.house"),
        ("Farrar Straus Giroux", "USA", "https://fsgbooks.com"),
        ("Avery Publishing", "USA", "https://penguin.co.uk"),
        ("Simon & Schuster", "USA", "https://simonandschuster.com"),
        ("Cambridge University Press", "UK", "https://cambridge.org"),
        ("Oxford University Press", "UK", "https://oup.com"),
    ]
    for p in publishers:
        db.execute("INSERT OR IGNORE INTO publishers (name,country,website) VALUES (?,?,?)", p)

    def pid(name):
        return db.execute("SELECT id FROM publishers WHERE name=?", (name,)).fetchone()["id"]

    # ── books ──
    raw_books = [
        # AI / ML
        ("9780134610993","Artificial Intelligence: A Modern Approach","The definitive textbook on artificial intelligence—search, knowledge, planning, learning, perception, and more.","Stuart Russell","Peter Norvig",pid("Pearson Education"),2020,"4th Edition",1132,"English","AI & Machine Learning","006.3 RUS","#1e3a5f",3),
        ("9781491950357","Hands-On Machine Learning with Scikit-Learn, Keras, and TensorFlow","Practical guide to building intelligent systems using Python's leading ML libraries.","Aurélien Géron",None,pid("O'Reilly Media"),2022,"3rd Edition",856,"English","AI & Machine Learning","006.31 GER","#2d3a4a",3),
        ("9780262035613","Deep Learning","Comprehensive treatment of deep neural networks by three leading researchers.","Ian Goodfellow","Yoshua Bengio",pid("MIT Press"),2016,"1st Edition",800,"English","AI & Machine Learning","006.32 GOO","#1a4a4a",2),
        ("9781492032649","Natural Language Processing with Transformers","Building language applications with Hugging Face and the transformer architecture.","Lewis Tunstall",None,pid("O'Reilly Media"),2022,"1st Edition",408,"English","AI & Machine Learning","006.35 TUN","#2d4a2d",2),
        ("9781617295362","Deep Learning with Python","Hands-on approach to deep learning using Python and Keras by the creator of Keras.","François Chollet",None,pid("Manning Publications"),2021,"2nd Edition",504,"English","AI & Machine Learning","006.32 CHO","#1e3a5f",2),
        ("9780262043793","Reinforcement Learning: An Introduction","The seminal textbook introducing Markov decision processes and RL algorithms.","Richard S. Sutton","Andrew G. Barto",pid("MIT Press"),2018,"2nd Edition",552,"English","AI & Machine Learning","006.31 SUT","#3d2b5c",2),
        ("9781491919521","Data Science from Scratch","First principles approach to implementing data science tools and algorithms in Python.","Joel Grus",None,pid("O'Reilly Media"),2019,"2nd Edition",403,"English","AI & Machine Learning","006.312 GRU","#1a4a4a",2),
        # Programming
        ("9780134494166","Clean Code: A Handbook of Agile Software Craftsmanship","Principles and practices of writing clean, maintainable, and readable code.","Robert C. Martin",None,pid("Prentice Hall"),2008,"1st Edition",431,"English","Programming","005.1 MAR","#5c1e1e",3),
        ("9780201633610","Design Patterns: Elements of Reusable Object-Oriented Software","The classic Gang of Four catalogue of 23 fundamental design patterns.","Erich Gamma","Richard Helm",pid("Addison-Wesley"),1994,"1st Edition",395,"English","Programming","005.1 GAM","#4a3520",2),
        ("9780132350884","Clean Architecture: A Craftsman's Guide to Software Structure and Design","Principles for organising code into maintainable, flexible architectures.","Robert C. Martin",None,pid("Prentice Hall"),2017,"1st Edition",432,"English","Programming","005.1 MAR","#5c1e1e",2),
        ("9780201485677","The Pragmatic Programmer: Your Journey to Mastery","Career advice and practical guidance for professional software developers.","David Thomas","Andrew Hunt",pid("Addison-Wesley"),2019,"20th Anniversary",352,"English","Programming","005.1 THO","#4a3520",3),
        ("9781491927281","Python Cookbook","Recipes for mastering Python's standard library, data structures, and concurrency.","David Beazley","Brian K. Jones",pid("O'Reilly Media"),2013,"3rd Edition",706,"English","Programming","005.133 BEA","#2d3a4a",2),
        ("9781593279288","Automate the Boring Stuff with Python","Practical programming for total beginners using Python for real-world automation.","Al Sweigart",None,pid("No Starch Press"),2019,"2nd Edition",592,"English","Programming","005.133 SWE","#1e3a5f",3),
        ("9780131103627","The C Programming Language","The original and authoritative guide to the C language by its creators.","Brian W. Kernighan","Dennis M. Ritchie",pid("Prentice Hall"),1988,"2nd Edition",274,"English","Programming","005.133 KER","#5c1e1e",2),
        ("9780596517748","JavaScript: The Good Parts","Identifying the elegant subset of JavaScript that enables reliable applications.","Douglas Crockford",None,pid("O'Reilly Media"),2008,"1st Edition",172,"English","Programming","005.133 CRO","#2d3a4a",2),
        ("9780134757599","The DevOps Handbook","How to create world-class agility, reliability, and security in technology organisations.","Gene Kim",None,pid("No Starch Press"),2021,"2nd Edition",480,"English","Programming","004.165 KIM","#1a4a4a",2),
        # Computer Science
        ("9780262033848","Introduction to Algorithms","Comprehensive treatment of algorithms and data structures—the standard textbook.","Thomas H. Cormen","Charles E. Leiserson",pid("MIT Press"),2022,"4th Edition",1312,"English","Computer Science","005.1 COR","#3a1e3a",3),
        ("9780134670959","Computer Organization and Architecture","Designing for performance in modern computer hardware and organisation.","William Stallings",None,pid("Pearson Education"),2018,"10th Edition",816,"English","Computer Science","004.22 STA","#1e2d4a",2),
        ("9781118063330","Operating System Concepts","The foundational textbook for understanding processes, memory, and file systems.","Abraham Silberschatz","Peter B. Galvin",pid("Wiley"),2018,"10th Edition",944,"English","Computer Science","005.43 SIL","#3d2b5c",2),
        ("9780136019701","Computer Networks","A top-down approach to networking from applications to physical layer.","Andrew S. Tanenbaum","David J. Wetherall",pid("Pearson Education"),2010,"5th Edition",960,"English","Computer Science","004.6 TAN","#1e3a5f",2),
        ("9780262024051","Database Systems: The Complete Book","Comprehensive coverage of database design, query languages, and implementation.","Hector Garcia-Molina",None,pid("Pearson Education"),2008,"2nd Edition",1203,"English","Computer Science","005.74 GAR","#4a2020",2),
        ("9780134685991","The Linux Command Line","A complete introduction to the Linux command line and shell scripting.","William Shotts",None,pid("No Starch Press"),2019,"2nd Edition",504,"English","Computer Science","005.432 SHO","#2d4a2d",3),
        # Data Science
        ("9781491912058","Python for Data Analysis","Data wrangling with pandas, NumPy, and Jupyter by the creator of pandas.","Wes McKinney",None,pid("O'Reilly Media"),2022,"3rd Edition",579,"English","Data Science","519.5 MCK","#1a4a4a",3),
        ("9781491901762","Hands-On Data Analysis with Pandas","Data manipulation and analysis in Python with practical case studies.","Stefanie Molin",None,pid("Packt Publishing"),2021,"2nd Edition",788,"English","Data Science","005.7 MOL","#2d3a4a",2),
        ("9781492041139","Data Science on the Google Cloud Platform","Implementing end-to-end data science workloads on GCP.","Valliappa Lakshmanan",None,pid("O'Reilly Media"),2022,"2nd Edition",558,"English","Data Science","004.678 LAK","#1e3a5f",2),
        ("9780262537131","The Art of Statistics","Learning from data with clear explanations of statistical thinking.","David Spiegelhalter",None,pid("MIT Press"),2019,"1st Edition",426,"English","Data Science","519.5 SPI","#3d2b5c",2),
        # Business & Management
        ("9780307887894","Zero to One: Notes on Startups","Contrarian thinking on building companies that create genuine value.","Peter Thiel","Blake Masters",pid("Crown Business"),2014,"1st Edition",224,"English","Business","658.11 THI","#4a3520",3),
        ("9780062316097","The Hard Thing About Hard Things","Building a business when there are no easy answers—raw startup honesty.","Ben Horowitz",None,pid("HarperCollins"),2014,"1st Edition",304,"English","Business","658.4 HOR","#5c1e1e",2),
        ("9781400032617","Good to Great: Why Some Companies Make the Leap","Findings from a five-year research project on how good companies become great ones.","Jim Collins",None,pid("HarperCollins"),2001,"1st Edition",320,"English","Business","658.406 COL","#4a3520",3),
        ("9780670921607","The Lean Startup","How today's entrepreneurs use continuous innovation to create successful businesses.","Eric Ries",None,pid("Crown Business"),2011,"1st Edition",299,"English","Business","658.11 RIE","#5c1e1e",2),
        ("9781501156700","Rich Dad Poor Dad","Financial education the school system never teaches—asset building vs liability building.","Robert T. Kiyosaki",None,pid("Simon & Schuster"),2017,"20th Anniversary",336,"English","Finance","332.024 KIY","#1e2d4a",3),
        ("9780525559474","The Psychology of Money","Timeless lessons on wealth, greed, and happiness—behavioural finance explained simply.","Morgan Housel",None,pid("Harriman House"),2020,"1st Edition",256,"English","Finance","332.024 HOU","#1a4a4a",3),
        ("9781400079674","A Random Walk Down Wall Street","The classic guide to successful investing strategies.","Burton G. Malkiel",None,pid("Simon & Schuster"),2019,"12th Edition",448,"English","Finance","332.6 MAL","#3a1e3a",2),
        ("9781119803539","Security Analysis","The seminal work on fundamental analysis of securities by Graham and Dodd.","Benjamin Graham","David L. Dodd",pid("McGraw-Hill"),2008,"6th Edition",736,"English","Finance","332.63 GRA","#1e2d4a",2),
        # Psychology & Cognitive Science
        ("9780374533557","Thinking, Fast and Slow","Kahneman's exploration of the two cognitive systems that drive human decisions.","Daniel Kahneman",None,pid("Farrar Straus Giroux"),2011,"1st Edition",499,"English","Psychology","153.4 KAH","#3a2d4a",2),
        ("9780062414236","Emotional Intelligence","Why emotional intelligence can matter more than IQ in personal and professional life.","Daniel Goleman",None,pid("Bloomsbury"),2005,"10th Anniversary",368,"English","Psychology","152.4 GOL","#4a3520",2),
        ("9780385333481","Influence: The Psychology of Persuasion","The foundational science behind why people say yes and how to apply these principles.","Robert B. Cialdini",None,pid("Simon & Schuster"),2006,"Revised Edition",336,"English","Psychology","153.8 CIA","#3d2b5c",2),
        ("9780385490443","The Power of Habit","Why we do what we do in life and business—the neuroscience of habit formation.","Charles Duhigg",None,pid("Crown Business"),2012,"1st Edition",371,"English","Psychology","153.32 DUH","#5c1e1e",2),
        # Self Development
        ("9781250301697","Atomic Habits","Proven framework for getting 1% better every day—tiny changes, remarkable results.","James Clear",None,pid("Avery Publishing"),2018,"1st Edition",320,"English","Self Development","158.1 CLE","#2d4a2d",4),
        ("9781501111105","Deep Work: Rules for Focused Success","The ability to perform focused work is becoming rare—and increasingly valuable.","Cal Newport",None,pid("Simon & Schuster"),2016,"1st Edition",296,"English","Self Development","153.4 NEW","#1e3a5f",3),
        ("9780671027032","How to Win Friends and Influence People","The landmark book on social skills and interpersonal effectiveness.","Dale Carnegie",None,pid("Simon & Schuster"),1936,"Revised Edition",291,"English","Self Development","158.2 CAR","#4a3520",3),
        ("9780316769174","The Catcher in the Rye","Holden Caulfield's journey through the phoniness of the adult world.","J.D. Salinger",None,pid("Bloomsbury"),1951,"Revised Edition",277,"English","Fiction","813.54 SAL","#2d4a2d",2),
        # History & Society
        ("9780062316110","Sapiens: A Brief History of Humankind","How Homo sapiens conquered the world—from the Stone Age to the twenty-first century.","Yuval Noah Harari",None,pid("HarperCollins"),2015,"1st Edition",443,"English","History","909 HAR","#3a1e3a",3),
        ("9780525560975","Homo Deus: A Brief History of Tomorrow","What will happen to jobs, consciousness, and political structures in the age of AI?","Yuval Noah Harari",None,pid("HarperCollins"),2017,"1st Edition",449,"English","History","303.49 HAR","#1e2d4a",2),
        ("9780374275631","The Innovators","How a group of hackers, geniuses, and geeks created the digital revolution.","Walter Isaacson",None,pid("Simon & Schuster"),2014,"1st Edition",560,"English","History","004.09 ISA","#4a3520",2),
        ("9781451648539","Steve Jobs","The exclusive biography of Apple's legendary co-founder.","Walter Isaacson",None,pid("Simon & Schuster"),2011,"1st Edition",630,"English","History","338.7 ISA","#5c1e1e",3),
        # Mathematics
        ("9780691166339","The Princeton Companion to Mathematics","An accessible guide to the major fields, concepts, and theorems of modern mathematics.","Timothy Gowers",None,pid("Cambridge University Press"),2008,"1st Edition",1008,"English","Mathematics","510 GOW","#3a2d4a",2),
        ("9780374239022","How to Solve It","Polya's classic guide to mathematical problem-solving strategies.","G. Polya",None,pid("Cambridge University Press"),2014,"Expanded Edition",288,"English","Mathematics","510 POL","#1a4a4a",2),
        ("9780521795401","Concrete Mathematics","A foundation for computer science mathematics from Knuth, Graham, and Patashnik.","Ronald L. Graham","Donald E. Knuth",pid("Addison-Wesley"),1994,"2nd Edition",672,"English","Mathematics","510 GRA","#3d2b5c",2),
        # Systems & Architecture
        ("9781449373320","Designing Data-Intensive Applications","The big ideas behind reliable, scalable, and maintainable systems.","Martin Kleppmann",None,pid("O'Reilly Media"),2017,"1st Edition",604,"English","Systems","005.74 KLE","#1e3a5f",3),
        ("9781492043447","Fundamentals of Software Architecture","A comprehensive guide to software architecture patterns and practices.","Mark Richards","Neal Ford",pid("O'Reilly Media"),2020,"1st Edition",419,"English","Systems","005.1 RIC","#2d4a2d",2),
        ("9780672337000","Computer Science Distilled","Learn the art of solving computational problems with key CS concepts.","Wladston Ferreira Filho",None,pid("No Starch Press"),2017,"1st Edition",180,"English","Systems","004 FIL","#4a2020",2),
        # Ethics & Society
        ("9780393634723","Weapons of Math Destruction","How big data and algorithms increase inequality and threaten democracy.","Cathy O'Neil",None,pid("Crown Business"),2016,"1st Edition",272,"English","Ethics & Society","303.483 ONE","#3a1e3a",2),
        ("9780525559023","The Alignment Problem","Machine learning and human values—how to build AI that does what we want.","Brian Christian",None,pid("No Starch Press"),2020,"1st Edition",496,"English","Ethics & Society","006.31 CHR","#1e2d4a",2),
        ("9781984823403","Race After Technology","Abolitionist tools for the new Jim Code—algorithmic discrimination.","Ruha Benjamin",None,pid("Wiley"),2019,"1st Edition",172,"English","Ethics & Society","305.8 BEN","#3d2b5c",2),
        # Research Methods
        ("9781138270053","Research Methods in Information Science","A practical guide to research design and methods for information professionals.","Alison Jane Pickard",None,pid("Oxford University Press"),2013,"2nd Edition",396,"English","Research Methods","020.72 PIC","#4a3520",2),
        ("9780804736268","The Craft of Research","Practical advice on researching, arguing, and writing for academic audiences.","Wayne C. Booth",None,pid("Oxford University Press"),2016,"4th Edition",336,"English","Research Methods","001.42 BOO","#2d4a2d",2),
        # Environment
        ("9780143136378","The Sixth Extinction","An unnatural history—the ongoing mass extinction event caused by human activity.","Elizabeth Kolbert",None,pid("Bloomsbury"),2014,"1st Edition",319,"English","Environment","576.84 KOL","#2d4a2d",2),
        ("9780062316578","The Uninhabitable Earth","Life after warming—a detailed portrait of the climate crisis.","David Wallace-Wells",None,pid("Crown Business"),2019,"1st Edition",320,"English","Environment","363.738 WAL","#1a4a4a",2),
    ]

    for r in raw_books:
        (isbn,title,desc,author1,author2,pub_id,year,edition,pages,lang,cat,dewey,color,copies) = r

        book_id = db.execute(
            "INSERT INTO books (isbn,title,description,publisher_id,pub_year,edition,pages,language,category,dewey,cover_color) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (isbn,title,desc,pub_id,year,edition,pages,lang,cat,dewey,color)
        ).lastrowid

        # Authors
        for aname in [a for a in [author1,author2] if a]:
            ex = db.execute("SELECT id FROM authors WHERE name=?", (aname,)).fetchone()
            if ex:
                aid = ex["id"]
            else:
                aid = db.execute("INSERT INTO authors (name) VALUES (?)", (aname,)).lastrowid
            db.execute("INSERT OR IGNORE INTO book_authors (book_id,author_id) VALUES (?,?)", (book_id,aid))

        # Tags from category + keywords
        tag_map = {
            "AI & Machine Learning": ["machine learning","artificial intelligence","deep learning","neural networks","python"],
            "Programming": ["software engineering","coding","development","best practices"],
            "Computer Science": ["algorithms","data structures","operating systems","networking"],
            "Data Science": ["pandas","data analysis","statistics","visualization"],
            "Business": ["entrepreneurship","management","strategy","leadership"],
            "Finance": ["investing","personal finance","money","economics"],
            "Psychology": ["cognitive science","behaviour","decision making","mental health"],
            "Self Development": ["productivity","habits","focus","personal growth"],
            "History": ["world history","biography","technology history"],
            "Mathematics": ["calculus","discrete math","problem solving","logic"],
            "Systems": ["distributed systems","architecture","databases","scalability"],
            "Ethics & Society": ["AI ethics","technology","social impact","policy"],
            "Research Methods": ["methodology","academic writing","data collection"],
            "Environment": ["climate change","ecology","sustainability"],
            "Fiction": ["literature","coming of age","classic"],
        }
        for tag_name in tag_map.get(cat, []):
            ex = db.execute("SELECT id FROM tags WHERE name=?", (tag_name,)).fetchone()
            tid = ex["id"] if ex else db.execute("INSERT INTO tags (name) VALUES (?)", (tag_name,)).lastrowid
            db.execute("INSERT OR IGNORE INTO book_tags (book_id,tag_id) VALUES (?,?)", (book_id,tid))

        # Item copies
        for n in range(1, copies+1):
            barcode = _make_barcode(book_id, n)
            conditions = ["Good","Good","Good","Fair"] if copies>1 else ["Good"]
            cond = conditions[min(n-1, len(conditions)-1)]
            db.execute("INSERT OR IGNORE INTO item_copies (book_id,barcode,condition) VALUES (?,?,?)", (book_id,barcode,cond))

    db.commit()

def _seed_admin(db):
    if not db.execute("SELECT id FROM users WHERE username='admin'").fetchone():
        hpw = bcrypt.hashpw("Admin@123".encode(), bcrypt.gensalt(BCRYPT_ROUNDS))
        db.execute(
            "INSERT INTO users (member_id,username,email,password,full_name,membership_type,is_admin) VALUES (?,?,?,?,?,?,1)",
            ("LIB000001","admin","admin@libms.com",hpw,"Library Administrator","Staff")
        )
        db.commit()

# ══════════════════════════════════════════════════════════════════
#  SECURITY HELPERS
# ══════════════════════════════════════════════════════════════════

def _hash_pw(pw):  return bcrypt.hashpw(pw.encode(), bcrypt.gensalt(BCRYPT_ROUNDS))
def _check_pw(pw, hashed):
    if isinstance(hashed,str): hashed=hashed.encode()
    return bcrypt.checkpw(pw.encode(), hashed)
def gen_csrf():
    if "_csrf" not in session: session["_csrf"] = secrets.token_hex(24)
    return session["_csrf"]
def valid_csrf(t): return secrets.compare_digest(session.get("_csrf",""), t or "")
def sanitise(t, mx=MAX_TEXT): return bleach.clean(t or "", tags=[], strip=True).strip()[:mx]
def get_ip(): return (request.headers.get("X-Forwarded-For", request.remote_addr) or "unknown").split(",")[0].strip()
def validate_username(u):
    if not u: return "Username is required."
    if not USERNAME_RE.match(u): return "Username: 3-32 characters, letters/numbers/underscore only."
    return None
def validate_password(p):
    if len(p)<8:               return "Password must be at least 8 characters."
    if not re.search(r'[A-Z]',p): return "Password must contain at least one uppercase letter."
    if not re.search(r'[0-9]',p): return "Password must contain at least one number."
    return None

@app.context_processor
def _ctx(): return {"csrf_token": gen_csrf(), "now": datetime.now()}

# ── Decorators ──

def login_required(f):
    @wraps(f)
    def w(*a,**kw):
        if "uid" not in session:
            flash("Please sign in to continue.","info"); return redirect("/login")
        last = session.get("_last")
        if last and (datetime.now()-datetime.fromisoformat(last)).seconds > 28800:
            session.clear(); flash("Session expired.","info"); return redirect("/login")
        session["_last"] = datetime.now().isoformat()
        return f(*a,**kw)
    return w

def admin_required(f):
    @wraps(f)
    def w(*a,**kw):
        if not session.get("is_admin"): abort(403)
        return f(*a,**kw)
    return w

def csrf_protect(f):
    @wraps(f)
    def w(*a,**kw):
        if request.method=="POST" and not valid_csrf(request.form.get("_csrf_token","")):
            flash("Security token invalid. Please try again.","error")
            return redirect(request.referrer or "/")
        return f(*a,**kw)
    return w


def init_dsa():
    with app.app_context():
        try:
            db = get_db()
            books = db.execute("SELECT b.id, b.title, b.category, (SELECT name FROM authors a JOIN book_authors ba ON ba.author_id=a.id WHERE ba.book_id=b.id LIMIT 1) as author FROM books b").fetchall()
            for b in books:
                search_trie.insert(b["title"], b["id"], b["author"] or "")
            
            tags = db.execute("SELECT book_id, tag_id FROM book_tags").fetchall()
            tag_to_books = {}
            for t in tags:
                tag_to_books.setdefault(t["tag_id"], []).append(t["book_id"])
            for t_id, b_list in tag_to_books.items():
                for i in range(len(b_list)):
                    for j in range(i+1, len(b_list)):
                        recommendation_graph.add_edge(b_list[i], b_list[j], weight=1)
            
            authors = db.execute("SELECT book_id, author_id FROM book_authors").fetchall()
            author_to_books = {}
            for a in authors:
                author_to_books.setdefault(a["author_id"], []).append(a["book_id"])
            for a_id, b_list in author_to_books.items():
                for i in range(len(b_list)):
                    for j in range(i+1, len(b_list)):
                        recommendation_graph.add_edge(b_list[i], b_list[j], weight=2)
            print("DSA Initialized")
        except Exception as e:
            pass

init_dsa()

# ── Utility ──


def calc_fine(due):
    d = datetime.strptime(due,"%Y-%m-%d")
    t = datetime.now().replace(hour=0,minute=0,second=0,microsecond=0)
    return max(0,(t-d).days*FINE_PER_DAY)

def notify(db,uid,msg,t="info"):
    db.execute("INSERT INTO notifications (user_id,message,type) VALUES (?,?,?)",(uid,msg[:300],t))

def log(db,uid,action,detail=""):
    db.execute("INSERT INTO activity_log (user_id,action,detail,ip) VALUES (?,?,?,?)",(uid,action,detail[:200],get_ip()))

def get_unread(db,uid):
    return db.execute("SELECT COUNT(*) FROM notifications WHERE user_id=? AND is_read=0",(uid,)).fetchone()[0]

def available_copies(db,book_id):
    total = db.execute("SELECT COUNT(*) FROM item_copies WHERE book_id=? AND is_active=1",(book_id,)).fetchone()[0]
    issued = db.execute("SELECT COUNT(*) FROM transactions WHERE book_id=? AND returned_at IS NULL",(book_id,)).fetchone()[0]
    return max(0, total - issued)

def get_book_authors(db,book_id):
    rows = db.execute("SELECT a.name FROM authors a JOIN book_authors ba ON ba.author_id=a.id WHERE ba.book_id=? ORDER BY ba.rowid",(book_id,)).fetchall()
    return ", ".join(r["name"] for r in rows)

def get_book_tags(db,book_id):
    return db.execute("SELECT t.name FROM tags t JOIN book_tags bt ON bt.tag_id=t.id WHERE bt.book_id=?",(book_id,)).fetchall()

def next_member_id(db):
    last = db.execute("SELECT member_id FROM users ORDER BY id DESC LIMIT 1").fetchone()
    n = int(last["member_id"].replace("LIB","")) + 1 if last else 2
    return f"LIB{n:06d}"

# ── Error pages ──

@app.errorhandler(403)
def e403(e): return render_template("error.html",code=403,title="Access Denied",msg="You do not have permission to view this page."),403
@app.errorhandler(404)
def e404(e): return render_template("error.html",code=404,title="Not Found",msg="The page you requested does not exist."),404
@app.errorhandler(429)
def e429(e): return render_template("error.html",code=429,title="Too Many Attempts",msg="Please wait before trying again."),429

# ══════════════════════════════════════════════════════════════════
#  AUTH
# ══════════════════════════════════════════════════════════════════

@app.route("/")
def index(): return redirect("/dashboard" if "uid" in session else "/login")

@app.route("/login", methods=["GET","POST"])
@limiter.limit("12 per minute")
@csrf_protect
def login():
    if "uid" in session: return redirect("/dashboard")
    if request.method=="POST":
        uname = sanitise(request.form.get("username",""),32)
        pw    = request.form.get("password","")
        if not uname or not pw: flash("Both fields are required.","error"); return render_template("login.html")
        db = get_db()
        u  = db.execute("SELECT * FROM users WHERE username=? COLLATE NOCASE",(uname,)).fetchone()
        if not u or not _check_pw(pw, u["password"]):
            log(db,None,"login_fail",f"username:{uname}"); db.commit()
            flash("Invalid username or password.","error"); return render_template("login.html")
        if u["is_banned"]: flash("Account suspended. Contact the library administrator.","error"); return render_template("login.html")
        session.clear(); session.permanent=True
        session.update(uid=u["id"],username=u["username"],is_admin=bool(u["is_admin"]),
                       full_name=u["full_name"] or u["username"],member_id=u["member_id"],
                       _last=datetime.now().isoformat())
        db.execute("UPDATE users SET last_login=?,login_count=login_count+1 WHERE id=?",
                   (datetime.now().strftime("%Y-%m-%d %H:%M:%S"),u["id"]))
        log(db,u["id"],"login"); db.commit()
        return redirect("/dashboard")
    return render_template("login.html")

@app.route("/signup", methods=["GET","POST"])
@limiter.limit("5 per minute")
@csrf_protect
def signup():
    if "uid" in session: return redirect("/dashboard")
    if request.method=="POST":
        uname  = sanitise(request.form.get("username",""),32)
        email  = sanitise(request.form.get("email",""),120).lower()
        pw     = request.form.get("password","")
        name   = sanitise(request.form.get("full_name",""),80)
        dept   = sanitise(request.form.get("department",""),80)
        mtype  = request.form.get("membership_type","Student")
        if mtype not in ("Student","Faculty","Staff"): mtype="Student"
        err = validate_username(uname)
        if err: flash(err,"error"); return render_template("signup.html")
        pw_err = validate_password(pw)
        if pw_err: flash(pw_err,"error"); return render_template("signup.html")
        if email and not EMAIL_RE.match(email): flash("Invalid email address.","error"); return render_template("signup.html")
        db = get_db()
        if db.execute("SELECT id FROM users WHERE username=? COLLATE NOCASE",(uname,)).fetchone():
            flash("Username already taken.","error"); return render_template("signup.html")
        if email and db.execute("SELECT id FROM users WHERE email=? COLLATE NOCASE",(email,)).fetchone():
            flash("Email already registered.","error"); return render_template("signup.html")
        mid = next_member_id(db)
        hpw = _hash_pw(pw)
        db.execute("INSERT INTO users (member_id,username,email,password,full_name,department,membership_type) VALUES (?,?,?,?,?,?,?)",
                   (mid,uname,email or None,hpw,name or None,dept or None,mtype))
        uid = db.execute("SELECT id FROM users WHERE username=?",(uname,)).fetchone()["id"]
        notify(db,uid,f"Welcome to LibMS. Your member ID is {mid}. Happy reading!","success")
        log(db,uid,"signup"); db.commit()
        flash("Account created. Please sign in.","success"); return redirect("/login")
    return render_template("signup.html")

@app.route("/logout")
def logout():
    if "uid" in session:
        db=get_db(); log(db,session["uid"],"logout"); db.commit()
    session.clear(); flash("You have been signed out.","info"); return redirect("/login")

# ══════════════════════════════════════════════════════════════════
#  DASHBOARD
# ══════════════════════════════════════════════════════════════════

@app.route("/dashboard")
@login_required
def dashboard():
    db = get_db()
    issued = db.execute("""
        SELECT t.id, t.due_date, t.issued_at, t.renewed,
               b.id as book_id, b.title, b.cover_color, b.category,
               ic.barcode, ic.location
        FROM transactions t
        JOIN books b ON t.book_id=b.id
        JOIN item_copies ic ON t.copy_id=ic.id
        WHERE t.user_id=? AND t.returned_at IS NULL ORDER BY t.due_date
    """,(session["uid"],)).fetchall()

    history = db.execute("""
        SELECT t.issued_at, t.returned_at, t.due_date,
               b.id as book_id, b.title, b.cover_color,
               COALESCE((SELECT amount FROM fines WHERE txn_id=t.id LIMIT 1),0) as fine_paid
        FROM transactions t JOIN books b ON t.book_id=b.id
        WHERE t.user_id=? AND t.returned_at IS NOT NULL
        ORDER BY t.returned_at DESC LIMIT 8
    """,(session["uid"],)).fetchall()

    holds = db.execute("""
        SELECT h.id, h.placed_at, h.status,
               b.id as book_id, b.title, b.cover_color,
               (SELECT COUNT(*) FROM holds h2 WHERE h2.book_id=b.id AND h2.status='waiting' AND h2.id<=h.id) as queue_pos,
               (SELECT COUNT(*) FROM item_copies WHERE book_id=b.id AND is_active=1) as total_copies,
               (SELECT COUNT(*) FROM transactions WHERE book_id=b.id AND returned_at IS NULL) as issued_count
        FROM holds h JOIN books b ON h.book_id=b.id
        WHERE h.user_id=? AND h.status IN ('waiting','ready') ORDER BY h.placed_at
    """,(session["uid"],)).fetchall()

    notifs = db.execute("SELECT * FROM notifications WHERE user_id=? ORDER BY created_at DESC LIMIT 10",(session["uid"],)).fetchall()
    pending_fines = db.execute("SELECT COALESCE(SUM(amount),0) as total FROM fines WHERE user_id=? AND paid=0",(session["uid"],)).fetchone()["total"]
    unread = get_unread(db,session["uid"])

    stats = {
        "issued":         len(issued),
        "total_borrowed": db.execute("SELECT COUNT(*) FROM transactions WHERE user_id=?",(session["uid"],)).fetchone()[0],
        "pending_fines":  pending_fines + sum(calc_fine(r["due_date"]) for r in issued if calc_fine(r["due_date"])>0),
        "holds":          len(holds),
    }
    today = datetime.now().strftime("%Y-%m-%d")
    books_data = []
    for r in issued:
        fine     = calc_fine(r["due_date"])
        overdue  = r["due_date"] < today
        days_left = (datetime.strptime(r["due_date"],"%Y-%m-%d")-datetime.now()).days
        books_data.append({**dict(r),"fine":fine,"overdue":overdue,"days_left":days_left,
                           "authors": get_book_authors(db, r["book_id"])})

    return render_template("dashboard.html", issued=books_data, history=history,
                           holds=holds, notifs=notifs, unread=unread, stats=stats, today=today)

# ══════════════════════════════════════════════════════════════════
#  CATALOGUE
# ══════════════════════════════════════════════════════════════════


@app.route("/api/autocomplete")
def autocomplete():
    q = request.args.get("q", "")
    if q:
        db = get_db()
        if "uid" in session:
            db.execute("INSERT INTO search_queries (user_id, query) VALUES (?, ?)", (session["uid"], q))
            db.commit()
    results = search_trie.search_prefix(q)
    return jsonify(results)

@app.route("/books")
@login_required
def books():
    q        = sanitise(request.args.get("q",""),100)
    category = sanitise(request.args.get("cat","All"),60)
    sort     = request.args.get("sort","title")
    if sort not in ("title","year","rating","availability"): sort="title"

    db = get_db()
    categories = ["All"] + [r[0] for r in db.execute("SELECT DISTINCT category FROM books ORDER BY category")]

    sql = """
        SELECT b.id, b.title, b.subtitle, b.pub_year, b.edition, b.pages, b.language,
               b.category, b.dewey, b.description, b.cover_color,
               p.name as publisher_name,
               COALESCE(AVG(r.rating),0) as avg_rating,
               COUNT(DISTINCT r.id) as review_count,
               (SELECT COUNT(*) FROM item_copies WHERE book_id=b.id AND is_active=1) as total_copies,
               (SELECT COUNT(*) FROM transactions WHERE book_id=b.id AND returned_at IS NULL) as issued_count
        FROM books b
        LEFT JOIN publishers p ON p.id=b.publisher_id
        LEFT JOIN reviews r ON r.book_id=b.id
    """
    filters, params = [], []
    if q:
        filters.append("""(lower(b.title) LIKE ? OR b.isbn LIKE ?
                           OR EXISTS(SELECT 1 FROM authors a JOIN book_authors ba ON ba.author_id=a.id WHERE ba.book_id=b.id AND lower(a.name) LIKE ?)
                           OR EXISTS(SELECT 1 FROM tags t JOIN book_tags bt ON bt.tag_id=t.id WHERE bt.book_id=b.id AND lower(t.name) LIKE ?))""")
        params += [f"%{q.lower()}%"]*4
    if category != "All": filters.append("b.category=?"); params.append(category)
    if filters: sql += " WHERE " + " AND ".join(filters)
    sql += " GROUP BY b.id ORDER BY " + {
        "title":"b.title","year":"b.pub_year DESC","rating":"avg_rating DESC",
        "availability":"(total_copies - issued_count) DESC, b.title"
    }[sort]

    all_books = db.execute(sql, params).fetchall()
    my_issued   = {r["book_id"] for r in db.execute("SELECT book_id FROM transactions WHERE user_id=? AND returned_at IS NULL",(session["uid"],))}
    my_holds    = {r["book_id"] for r in db.execute("SELECT book_id FROM holds WHERE user_id=? AND status IN ('waiting','ready')",(session["uid"],))}
    unread = get_unread(db,session["uid"])

    books_list = []
    for b in all_books:
        avail = b["total_copies"] - b["issued_count"]
        books_list.append({
            **dict(b),
            "available":   avail,
            "my_issued":   b["id"] in my_issued,
            "my_hold":     b["id"] in my_holds,
            "authors":     get_book_authors(db, b["id"]),
        })

    return render_template("books.html", books=books_list, categories=categories,
                           current_cat=category, q=q, sort=sort, unread=unread,
                           total=len(books_list))

@app.route("/book/<int:book_id>")
@login_required
def book_detail(book_id):
    db = get_db()
    cached_data = catalogue_cache.get(book_id)
    if cached_data:
        b, authors, tags = cached_data
    else:
        row = db.execute("""
            SELECT b.*, p.name as publisher_name,
                   COALESCE(AVG(r.rating),0) as avg_rating, COUNT(DISTINCT r.id) as review_count,
                   (SELECT COUNT(*) FROM item_copies WHERE book_id=b.id AND is_active=1) as total_copies
            FROM books b LEFT JOIN publishers p ON p.id=b.publisher_id
            LEFT JOIN reviews r ON r.book_id=b.id WHERE b.id=?
        """,(book_id,)).fetchone()
        if not row: abort(404)
        b = dict(row)
        authors  = get_book_authors(db,book_id)
        tags     = get_book_tags(db,book_id)
        catalogue_cache.put(book_id, (b, authors, tags))
    
    issued_count = db.execute("SELECT COUNT(*) FROM transactions WHERE book_id=? AND returned_at IS NULL",(book_id,)).fetchone()[0]
    avail      = b["total_copies"] - issued_count
    
    copies   = db.execute("SELECT * FROM item_copies WHERE book_id=? AND is_active=1 ORDER BY barcode",(book_id,)).fetchall()
    reviews  = db.execute("SELECT r.*, u.username, u.full_name FROM reviews r JOIN users u ON r.user_id=u.id WHERE r.book_id=? ORDER BY r.created_at DESC",(book_id,)).fetchall()
    my_review  = db.execute("SELECT * FROM reviews WHERE user_id=? AND book_id=?",(session["uid"],book_id)).fetchone()
    my_issued  = db.execute("SELECT t.id, t.renewed, t.due_date, ic.barcode FROM transactions t JOIN item_copies ic ON t.copy_id=ic.id WHERE t.user_id=? AND t.book_id=? AND t.returned_at IS NULL",(session["uid"],book_id)).fetchone()
    my_hold    = db.execute("SELECT id, status FROM holds WHERE user_id=? AND book_id=? AND status IN ('waiting','ready')",(session["uid"],book_id)).fetchone()
    unread     = get_unread(db,session["uid"])
    hold_count = db.execute("SELECT COUNT(*) FROM holds WHERE book_id=? AND status='waiting'",(book_id,)).fetchone()[0]

    # Graph-based related books (BFS)
    recommended_ids = recommendation_graph.get_recommendations(book_id, limit=4)
    related = []
    if recommended_ids:
        placeholders = ','.join('?' for _ in recommended_ids)
        related_rows = db.execute(f"SELECT id, title, cover_color FROM books WHERE id IN ({placeholders})", recommended_ids).fetchall()
        for rb in related_rows:
            rb = dict(rb)
            rb["authors"] = get_book_authors(db, rb["id"])
            related.append(rb)

    return render_template("book_detail.html", b=b, authors=authors, tags=tags, copies=copies,
                           reviews=reviews, my_review=my_review, my_issued=my_issued,
                           my_hold=my_hold, avail=avail, hold_count=hold_count,
                           related=related, unread=unread)

# ══════════════════════════════════════════════════════════════════
#  CIRCULATION
# ══════════════════════════════════════════════════════════════════

@app.route("/issue/<int:book_id>")
@login_required
def issue(book_id):
    db   = get_db()
    book = db.execute("SELECT * FROM books WHERE id=?",(book_id,)).fetchone()
    if not book: abort(404)
    held = db.execute("SELECT COUNT(*) FROM transactions WHERE user_id=? AND returned_at IS NULL",(session["uid"],)).fetchone()[0]
    if held >= MAX_BOOKS: flash(f"You may hold at most {MAX_BOOKS} books at a time.","error"); return redirect("/books")
    if db.execute("SELECT id FROM transactions WHERE user_id=? AND book_id=? AND returned_at IS NULL",(session["uid"],book_id)).fetchone():
        flash("You already have this book.","error"); return redirect("/books")
    # Find a free copy
    copy = db.execute("""SELECT ic.id FROM item_copies ic WHERE ic.book_id=? AND ic.is_active=1
        AND ic.id NOT IN (SELECT copy_id FROM transactions WHERE returned_at IS NULL)
        ORDER BY ic.condition='Good' DESC LIMIT 1""",(book_id,)).fetchone()
    if not copy: flash("No copies available. You may place a hold.","error"); return redirect(f"/book/{book_id}")
    now = datetime.now().strftime("%Y-%m-%d")
    due = (datetime.now()+timedelta(days=BORROW_DAYS)).strftime("%Y-%m-%d")
    db.execute("INSERT INTO transactions (user_id,copy_id,book_id,issued_at,due_date) VALUES (?,?,?,?,?)",
               (session["uid"],copy["id"],book_id,now,due))
    db.execute("UPDATE holds SET status='fulfilled' WHERE user_id=? AND book_id=?",(session["uid"],book_id))
    notify(db,session["uid"],f"'{book['title']}' issued. Due {due}.","success")
    log(db,session["uid"],"issue",f"book_id:{book_id}"); db.commit()
    flash(f"'{book['title']}' issued. Due by {due}.","success"); return redirect("/dashboard")

@app.route("/return/<int:txn_id>")
@login_required
def return_book(txn_id):
    db  = get_db()
    txn = db.execute("SELECT t.*, b.title FROM transactions t JOIN books b ON t.book_id=b.id WHERE t.id=? AND t.user_id=? AND t.returned_at IS NULL",(txn_id,session["uid"])).fetchone()
    if not txn: abort(404)
    fine = calc_fine(txn["due_date"])
    now  = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    db.execute("UPDATE transactions SET returned_at=? WHERE id=?",(now,txn_id))
    if fine > 0:
        db.execute("INSERT INTO fines (user_id,txn_id,amount,reason) VALUES (?,?,?,?)",
                   (session["uid"],txn_id,fine,f"Overdue — returned {now[:10]}"))
    # Notify next person in hold queue (Priority Queue via DB)
    next_hold = db.execute("""
        SELECT h.id, h.user_id 
        FROM holds h JOIN users u ON h.user_id = u.id 
        WHERE h.book_id=? AND h.status='waiting' 
        ORDER BY u.priority_level DESC, h.placed_at ASC LIMIT 1
    """,(txn["book_id"],)).fetchone()
    if next_hold:
        bk = db.execute("SELECT title FROM books WHERE id=?",(txn["book_id"],)).fetchone()
        db.execute("UPDATE holds SET status='ready', notified=1 WHERE id=?",(next_hold["id"],))
        notify(db,next_hold["user_id"],f"'{bk['title']}' is now available. Your hold is ready — please collect within 3 days.","success")
    msg = f"'{txn['title']}' returned." + (f" Fine applied: Rs. {fine}." if fine else " No fine.")
    notify(db,session["uid"],msg,"warning" if fine else "success")
    log(db,session["uid"],"return",f"txn_id:{txn_id} fine:{fine}"); db.commit()
    flash(msg,"warning" if fine else "success"); return redirect("/dashboard")

@app.route("/renew/<int:txn_id>")
@login_required
def renew(txn_id):
    db  = get_db()
    txn = db.execute("SELECT * FROM transactions WHERE id=? AND user_id=? AND returned_at IS NULL",(txn_id,session["uid"])).fetchone()
    if not txn: abort(404)
    if txn["renewed"]: flash("Each book can be renewed only once.","error"); return redirect("/dashboard")
    if calc_fine(txn["due_date"])>0: flash("Cannot renew an overdue book.","error"); return redirect("/dashboard")
    # Check no holds are waiting
    holds_waiting = db.execute("SELECT COUNT(*) FROM holds WHERE book_id=? AND status='waiting'",(txn["book_id"],)).fetchone()[0]
    if holds_waiting: flash("Renewal not available — other members are waiting for this title.","error"); return redirect("/dashboard")
    new_due = (datetime.strptime(txn["due_date"],"%Y-%m-%d")+timedelta(days=BORROW_DAYS)).strftime("%Y-%m-%d")
    db.execute("UPDATE transactions SET due_date=?, renewed=1 WHERE id=?",(new_due,txn_id))
    notify(db,session["uid"],f"Book renewed. New due date: {new_due}.","info")
    log(db,session["uid"],"renew",f"txn_id:{txn_id}"); db.commit()
    flash(f"Renewed. New due date: {new_due}.","success"); return redirect("/dashboard")

@app.route("/hold/<int:book_id>")
@login_required
def place_hold(book_id):
    db   = get_db()
    book = db.execute("SELECT * FROM books WHERE id=?",(book_id,)).fetchone()
    if not book: abort(404)
    if db.execute("SELECT id FROM holds WHERE user_id=? AND book_id=? AND status IN ('waiting','ready')",(session["uid"],book_id)).fetchone():
        flash("You already have a hold on this book.","error"); return redirect(f"/book/{book_id}")
    db.execute("INSERT OR IGNORE INTO holds (user_id,book_id) VALUES (?,?)",(session["uid"],book_id))
    pos = db.execute("SELECT COUNT(*) FROM holds WHERE book_id=? AND status='waiting'",(book_id,)).fetchone()[0]
    notify(db,session["uid"],f"Hold placed for '{book['title']}'. Queue position: {pos}.","info")
    log(db,session["uid"],"hold",f"book_id:{book_id}"); db.commit()
    flash(f"Hold placed. Queue position: {pos}.","success"); return redirect(f"/book/{book_id}")

@app.route("/cancel_hold/<int:hold_id>")
@login_required
def cancel_hold(hold_id):
    db = get_db()
    db.execute("UPDATE holds SET status='cancelled' WHERE id=? AND user_id=?",(hold_id,session["uid"])); db.commit()
    flash("Hold cancelled.","success"); return redirect("/dashboard")

# ══════════════════════════════════════════════════════════════════
#  REVIEWS
# ══════════════════════════════════════════════════════════════════

@app.route("/review/<int:book_id>", methods=["POST"])
@login_required
@csrf_protect
def submit_review(book_id):
    db = get_db()
    if not db.execute("SELECT id FROM books WHERE id=?",(book_id,)).fetchone(): abort(404)
    try: rating=int(request.form.get("rating",0))
    except ValueError: rating=0
    if not 1<=rating<=5: flash("Please select a rating.","error"); return redirect(f"/book/{book_id}")
    comment = sanitise(request.form.get("comment",""), MAX_TEXT)
    ex = db.execute("SELECT id FROM reviews WHERE user_id=? AND book_id=?",(session["uid"],book_id)).fetchone()
    if ex:
        db.execute("UPDATE reviews SET rating=?,comment=? WHERE id=?",(rating,comment,ex["id"]))
        flash("Review updated.","success")
    else:
        db.execute("INSERT INTO reviews (user_id,book_id,rating,comment) VALUES (?,?,?,?)",(session["uid"],book_id,rating,comment))
        flash("Review submitted.","success")
    db.commit(); return redirect(f"/book/{book_id}")

# ══════════════════════════════════════════════════════════════════
#  ACQUISITIONS
# ══════════════════════════════════════════════════════════════════

@app.route("/suggest", methods=["GET","POST"])
@login_required
@csrf_protect
def suggest():
    db = get_db()
    if request.method=="POST":
        title  = sanitise(request.form.get("title",""),200)
        author = sanitise(request.form.get("author",""),200)
        isbn   = sanitise(request.form.get("isbn",""),20)
        reason = sanitise(request.form.get("reason",""),500)
        if not title: flash("Title is required.","error"); return redirect("/suggest")
        db.execute("INSERT INTO acquisitions (user_id,title,author,isbn,reason) VALUES (?,?,?,?,?)",
                   (session["uid"],title,author or None,isbn or None,reason or None))
        notify(db,session["uid"],f"Book suggestion '{title}' submitted. The library team will review it.","info")
        log(db,session["uid"],"suggest",f"title:{title}"); db.commit()
        flash("Suggestion submitted. Thank you!","success"); return redirect("/suggest")
    my_suggestions = db.execute("SELECT * FROM acquisitions WHERE user_id=? ORDER BY created_at DESC",(session["uid"],)).fetchall()
    unread = get_unread(db,session["uid"])
    return render_template("suggest.html", suggestions=my_suggestions, unread=unread)

# ══════════════════════════════════════════════════════════════════
#  FINES
# ══════════════════════════════════════════════════════════════════

@app.route("/fines")
@login_required
def fines():
    db = get_db()
    active_txns = db.execute("""
        SELECT t.id, t.due_date, b.id as book_id, b.title, b.cover_color
        FROM transactions t JOIN books b ON t.book_id=b.id
        WHERE t.user_id=? AND t.returned_at IS NULL
    """,(session["uid"],)).fetchall()
    fine_records = db.execute("""
        SELECT f.*, b.title FROM fines f
        JOIN transactions t ON f.txn_id=t.id JOIN books b ON t.book_id=b.id
        WHERE f.user_id=? ORDER BY f.created_at DESC LIMIT 20
    """,(session["uid"],)).fetchall()
    accruing = [{**dict(r),"fine":calc_fine(r["due_date"])} for r in active_txns if calc_fine(r["due_date"])>0]
    total_accruing  = sum(r["fine"] for r in accruing)
    total_unpaid    = db.execute("SELECT COALESCE(SUM(amount),0) FROM fines WHERE user_id=? AND paid=0",(session["uid"],)).fetchone()[0]
    total_paid      = db.execute("SELECT COALESCE(SUM(amount),0) FROM fines WHERE user_id=? AND paid=1",(session["uid"],)).fetchone()[0]
    unread = get_unread(db,session["uid"])
    return render_template("fines.html", accruing=accruing, records=fine_records,
                           total_accruing=total_accruing, total_unpaid=total_unpaid,
                           total_paid=total_paid, unread=unread)

# ══════════════════════════════════════════════════════════════════
#  NOTIFICATIONS
# ══════════════════════════════════════════════════════════════════

@app.route("/notifications")
@login_required
def notifications():
    db = get_db()
    db.execute("UPDATE notifications SET is_read=1 WHERE user_id=?",(session["uid"],))
    notifs = db.execute("SELECT * FROM notifications WHERE user_id=? ORDER BY created_at DESC LIMIT 100",(session["uid"],)).fetchall()
    db.commit()
    return render_template("notifications.html", notifs=notifs, unread=0)

@app.route("/api/notif_count")
@login_required
def notif_count():
    return jsonify({"count": get_unread(get_db(),session["uid"])})

# ══════════════════════════════════════════════════════════════════
#  PROFILE
# ══════════════════════════════════════════════════════════════════

@app.route("/profile", methods=["GET","POST"])
@login_required
@csrf_protect
def profile():
    db   = get_db()
    user = db.execute("SELECT * FROM users WHERE id=?",(session["uid"],)).fetchone()
    stats = {
        "total":   db.execute("SELECT COUNT(*) FROM transactions WHERE user_id=?",(session["uid"],)).fetchone()[0],
        "active":  db.execute("SELECT COUNT(*) FROM transactions WHERE user_id=? AND returned_at IS NULL",(session["uid"],)).fetchone()[0],
        "fines":   db.execute("SELECT COALESCE(SUM(amount),0) FROM fines WHERE user_id=? AND paid=1",(session["uid"],)).fetchone()[0],
        "reviews": db.execute("SELECT COUNT(*) FROM reviews WHERE user_id=?",(session["uid"],)).fetchone()[0],
        "holds":   db.execute("SELECT COUNT(*) FROM holds WHERE user_id=? AND status IN ('waiting','ready')",(session["uid"],)).fetchone()[0],
        "suggestions": db.execute("SELECT COUNT(*) FROM acquisitions WHERE user_id=?",(session["uid"],)).fetchone()[0],
    }
    history = db.execute("""
        SELECT t.issued_at, t.returned_at, t.due_date, b.id as book_id, b.title, b.cover_color, b.category
        FROM transactions t JOIN books b ON t.book_id=b.id
        WHERE t.user_id=? ORDER BY t.issued_at DESC LIMIT 20
    """,(session["uid"],)).fetchall()
    unread = get_unread(db,session["uid"])

    if request.method=="POST":
        fn  = sanitise(request.form.get("full_name",""),80)
        em  = sanitise(request.form.get("email",""),120).lower()
        ph  = sanitise(request.form.get("phone",""),20)
        dept= sanitise(request.form.get("department",""),80)
        pw  = request.form.get("new_password","").strip()
        cpw = request.form.get("confirm_password","").strip()
        if em and not EMAIL_RE.match(em): flash("Invalid email address.","error"); return redirect("/profile")
        if pw:
            pw_err = validate_password(pw)
            if pw_err: flash(pw_err,"error"); return redirect("/profile")
            if pw!=cpw: flash("Passwords do not match.","error"); return redirect("/profile")
        up,pa = [],[]
        if fn:   up.append("full_name=?");  pa.append(fn)
        if em:   up.append("email=?");      pa.append(em)
        if ph:   up.append("phone=?");      pa.append(ph)
        if dept: up.append("department=?"); pa.append(dept)
        if pw:   up.append("password=?");   pa.append(_hash_pw(pw))
        if up:
            pa.append(session["uid"])
            db.execute(f"UPDATE users SET {','.join(up)} WHERE id=?",pa)
            if fn: session["full_name"]=fn
            notify(db,session["uid"],"Profile updated.","info"); db.commit()
            flash("Profile updated.","success")
        return redirect("/profile")
    return render_template("profile.html", user=user, stats=stats, history=history, unread=unread)

# ══════════════════════════════════════════════════════════════════
#  ADMIN PANEL
# ══════════════════════════════════════════════════════════════════

@app.route("/admin")
@login_required
@admin_required
def admin():
    db    = get_db()
    users = db.execute("""
        SELECT u.*, (SELECT COUNT(*) FROM transactions t2 WHERE t2.user_id=u.id AND t2.returned_at IS NULL) as active_books
        FROM users u ORDER BY u.joined_at DESC
    """).fetchall()
    books = db.execute("""
        SELECT b.id, b.title, b.cover_color, b.category, b.dewey, b.pub_year,
               p.name as publisher_name,
               (SELECT COUNT(*) FROM item_copies WHERE book_id=b.id AND is_active=1) as total_copies,
               (SELECT COUNT(*) FROM transactions WHERE book_id=b.id AND returned_at IS NULL) as issued_now,
               (SELECT COUNT(*) FROM transactions WHERE book_id=b.id) as total_issues,
               COALESCE((SELECT AVG(rating) FROM reviews WHERE book_id=b.id),0) as avg_rating
        FROM books b LEFT JOIN publishers p ON p.id=b.publisher_id
        ORDER BY b.category, b.title
    """).fetchall()
    active_txns = db.execute("""
        SELECT t.id, t.issued_at, t.due_date, t.renewed,
               u.username, u.full_name, u.member_id,
               b.id as book_id, b.title, b.cover_color,
               ic.barcode
        FROM transactions t
        JOIN users u ON t.user_id=u.id
        JOIN books b ON t.book_id=b.id
        JOIN item_copies ic ON t.copy_id=ic.id
        WHERE t.returned_at IS NULL ORDER BY t.due_date
    """).fetchall()
    acquisitions = db.execute("""
        SELECT aq.*, u.username FROM acquisitions aq
        LEFT JOIN users u ON aq.user_id=u.id ORDER BY aq.created_at DESC LIMIT 30
    """).fetchall()
    stats = {
        "users":      db.execute("SELECT COUNT(*) FROM users WHERE is_admin=0").fetchone()[0],
        "books":      db.execute("SELECT COUNT(*) FROM books").fetchone()[0],
        "copies":     db.execute("SELECT COUNT(*) FROM item_copies WHERE is_active=1").fetchone()[0],
        "issued":     db.execute("SELECT COUNT(*) FROM transactions WHERE returned_at IS NULL").fetchone()[0],
        "overdue":    0,
        "fines_total":db.execute("SELECT COALESCE(SUM(amount),0) FROM fines").fetchone()[0],
        "fines_unpaid":db.execute("SELECT COALESCE(SUM(amount),0) FROM fines WHERE paid=0").fetchone()[0],
        "holds":      db.execute("SELECT COUNT(*) FROM holds WHERE status='waiting'").fetchone()[0],
        "suggestions":db.execute("SELECT COUNT(*) FROM acquisitions WHERE status='pending'").fetchone()[0],
    }
    today = datetime.now().strftime("%Y-%m-%d")
    unread= get_unread(db,session["uid"])
    txns_data, overdue_count = [], 0
    for t in active_txns:
        fine=calc_fine(t["due_date"]); overdue=t["due_date"]<today
        if overdue: overdue_count+=1
        txns_data.append({**dict(t),"fine":fine,"overdue":overdue})
    stats["overdue"] = overdue_count

    # Reports data
    top_books = db.execute("""
        SELECT b.title, b.cover_color, COUNT(t.id) as borrow_count
        FROM transactions t JOIN books b ON t.book_id=b.id
        GROUP BY b.id ORDER BY borrow_count DESC LIMIT 8
    """).fetchall()
    cat_stats = [dict(row) for row in db.execute("""
        SELECT b.category, COUNT(t.id) as cnt
        FROM transactions t JOIN books b ON t.book_id=b.id
        GROUP BY b.category ORDER BY cnt DESC
    """).fetchall()]

    categories = [r[0] for r in db.execute("SELECT DISTINCT category FROM books ORDER BY category")]
    publishers = db.execute("SELECT id, name FROM publishers ORDER BY name").fetchall()

    return render_template("admin.html",
        users=users, books=books, txns=txns_data, acquisitions=acquisitions,
        stats=stats, today=today, unread=unread, top_books=top_books,
        cat_stats=cat_stats, categories=categories, publishers=publishers)

@app.route("/admin/add_book", methods=["POST"])
@login_required
@admin_required
@csrf_protect
def admin_add_book():
    f=request.form
    title=sanitise(f.get("title",""),200); author=sanitise(f.get("author",""),200); cat=sanitise(f.get("category",""),80)
    if not title or not author or not cat: flash("Title, author, category required.","error"); return redirect("/admin")
    try: year=int(f.get("year")) if f.get("year") else None; copies=max(1,int(f.get("copies",1)))
    except ValueError: flash("Year/copies must be numbers.","error"); return redirect("/admin")
    pub_id=int(f.get("publisher_id")) if f.get("publisher_id") else None
    db=get_db()
    color=random.choice(COVER_COLORS)
    try:
        bid=db.execute("INSERT INTO books (isbn,title,subtitle,publisher_id,pub_year,edition,pages,language,category,dewey,description,cover_color) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (sanitise(f.get("isbn",""),20) or None,title,sanitise(f.get("subtitle",""),200) or None,
             pub_id,year,sanitise(f.get("edition",""),50) or None,
             int(f.get("pages")) if f.get("pages") else None,
             sanitise(f.get("language","English"),40),cat,sanitise(f.get("dewey",""),30) or None,
             sanitise(f.get("description",""),MAX_TEXT) or None,color)).lastrowid
        # Add author
        ex=db.execute("SELECT id FROM authors WHERE name=?",(author,)).fetchone()
        aid=ex["id"] if ex else db.execute("INSERT INTO authors (name) VALUES (?)",(author,)).lastrowid
        db.execute("INSERT OR IGNORE INTO book_authors (book_id,author_id) VALUES (?,?)",(bid,aid))
        # Add copies
        for n in range(1,copies+1):
            db.execute("INSERT OR IGNORE INTO item_copies (book_id,barcode,condition) VALUES (?,?,?)",(bid,_make_barcode(bid,n),"Good"))
        db.commit(); flash(f"'{title}' added with {copies} cop{'y' if copies==1 else 'ies'}.","success")
    except Exception as e: flash(f"Error: {e}","error")
    return redirect("/admin")

@app.route("/admin/delete_book/<int:book_id>")
@login_required
@admin_required
def admin_delete_book(book_id):
    db=get_db()
    if db.execute("SELECT COUNT(*) FROM transactions WHERE book_id=? AND returned_at IS NULL",(book_id,)).fetchone()[0]:
        flash("Cannot delete — copies currently issued.","error"); return redirect("/admin")
    db.execute("DELETE FROM books WHERE id=?",(book_id,)); db.commit()
    flash("Book deleted.","success"); return redirect("/admin")

@app.route("/admin/edit_book/<int:book_id>", methods=["POST"])
@login_required
@admin_required
@csrf_protect
def admin_edit_book(book_id):
    f = request.form
    title = sanitise(f.get("title", ""), 200)
    cat = sanitise(f.get("category", ""), 80)
    
    if not title or not cat:
        flash("Title and category are required.","error")
        return redirect("/admin")
        
    try: 
        year = int(f.get("year")) if f.get("year") else None
        target_copies = max(1, int(f.get("copies", 1)))
    except ValueError:
        flash("Year and copies must be numbers.", "error")
        return redirect("/admin")
        
    db = get_db()
    book = db.execute("SELECT * FROM books WHERE id=?", (book_id,)).fetchone()
    if not book:
        abort(404)
        
    db.execute("UPDATE books SET title=?, pub_year=?, category=? WHERE id=?", 
               (title, year, cat, book_id))
               
    # Handle copies update
    current_copies = db.execute("SELECT COUNT(*) FROM item_copies WHERE book_id=? AND is_active=1", (book_id,)).fetchone()[0]
    
    if target_copies > current_copies:
        # Add more copies
        for n in range(current_copies + 1, target_copies + 1):
            db.execute("INSERT OR IGNORE INTO item_copies (book_id,barcode,condition) VALUES (?,?,?)",
                       (book_id, _make_barcode(book_id, n), "Good"))
    elif target_copies < current_copies:
        # Can only remove copies if they aren't currently issued
        issued_count = db.execute("SELECT COUNT(*) FROM transactions WHERE book_id=? AND returned_at IS NULL", (book_id,)).fetchone()[0]
        if target_copies < issued_count:
            flash(f"Cannot reduce to {target_copies} copies because {issued_count} are currently issued.", "error")
        else:
            # Determine how many to deactivate
            to_remove = current_copies - target_copies
            copies_to_deactivate = db.execute("""
                SELECT id FROM item_copies 
                WHERE book_id=? AND is_active=1 
                AND id NOT IN (SELECT copy_id FROM transactions WHERE returned_at IS NULL)
                LIMIT ?
            """, (book_id, to_remove)).fetchall()
            
            for copy in copies_to_deactivate:
                db.execute("UPDATE item_copies SET is_active=0 WHERE id=?", (copy["id"],))
                
    db.commit()
    flash(f"Book '{title}' updated successfully.", "success")
    return redirect("/admin")

@app.route("/admin/toggle_ban/<int:user_id>")
@login_required
@admin_required
def toggle_ban(user_id):
    if user_id==session["uid"]: flash("Cannot modify your own account.","error"); return redirect("/admin")
    db=get_db(); u=db.execute("SELECT is_banned,username FROM users WHERE id=?",(user_id,)).fetchone()
    if not u: abort(404)
    new=1-u["is_banned"]; db.execute("UPDATE users SET is_banned=? WHERE id=?",(new,user_id))
    notify(db,user_id,"Account reinstated." if not new else "Library access suspended.","info" if not new else "warning")
    log(db,session["uid"],"ban_toggle",f"target:{u['username']} status:{new}"); db.commit()
    return redirect("/admin")

@app.route("/admin/force_return/<int:txn_id>")
@login_required
@admin_required
def admin_force_return(txn_id):
    db=get_db(); txn=db.execute("SELECT * FROM transactions WHERE id=?",(txn_id,)).fetchone()
    if not txn: abort(404)
    fine=calc_fine(txn["due_date"]); now=datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    db.execute("UPDATE transactions SET returned_at=? WHERE id=?",(now,txn_id))
    if fine>0: db.execute("INSERT INTO fines (user_id,txn_id,amount,reason) VALUES (?,?,?,?)",(txn["user_id"],txn_id,fine,"Overdue (admin return)"))
    notify(db,txn["user_id"],f"A book was returned on your behalf by the administrator.{' Fine: Rs.'+str(fine)+'.' if fine else ''}","warning")
    log(db,session["uid"],"admin_force_return",f"txn_id:{txn_id}"); db.commit()
    flash("Book returned.","success"); return redirect("/admin")

@app.route("/admin/acquisition/<int:acq_id>/<action>")
@login_required
@admin_required
def admin_acquisition(acq_id, action):
    if action not in ("approve","reject","order"): abort(404)
    db=get_db(); acq=db.execute("SELECT * FROM acquisitions WHERE id=?",(acq_id,)).fetchone()
    if not acq: abort(404)
    db.execute("UPDATE acquisitions SET status=? WHERE id=?",(action+"d" if action=="reject" else action+"ed",acq_id))
    if acq["user_id"]:
        msgs={"approve":"Your book suggestion has been approved.","reject":"Your book suggestion was not approved at this time.","order":"Your suggested book has been ordered!"}
        notify(db,acq["user_id"],msgs.get(action,""),{"approve":"success","reject":"info","order":"success"}.get(action,"info"))
    log(db,session["uid"],"acquisition_action",f"acq_id:{acq_id} action:{action}"); db.commit()
    flash(f"Suggestion {action}ed.","success"); return redirect("/admin")

@app.route("/admin/notify_user/<int:user_id>", methods=["POST"])
@login_required
@admin_required
@csrf_protect
def admin_notify(user_id):
    msg=sanitise(request.form.get("message",""),300)
    if not msg: flash("Message required.","error"); return redirect("/admin")
    db=get_db()
    if not db.execute("SELECT id FROM users WHERE id=?",(user_id,)).fetchone(): abort(404)
    notify(db,user_id,msg,"info"); log(db,session["uid"],"admin_notify",f"target:{user_id}"); db.commit()
    flash("Notification sent.","success"); return redirect("/admin")


# ══════════════════════════════════════════════════════════════════
#  SEARCH API (live search)
# ══════════════════════════════════════════════════════════════════

@app.route("/api/search")
@login_required
def api_search():
    q     = sanitise(request.args.get("q",""), 100)
    limit = min(int(request.args.get("limit", 6)), 12)
    if not q or len(q) < 2:
        return jsonify({"results": []})
    db = get_db()
    rows = db.execute("""
        SELECT b.id, b.title, b.cover_color, b.category,
               (SELECT COUNT(*) FROM item_copies WHERE book_id=b.id AND is_active=1) -
               (SELECT COUNT(*) FROM transactions WHERE book_id=b.id AND returned_at IS NULL) as available
        FROM books b
        WHERE lower(b.title) LIKE ?
           OR b.isbn LIKE ?
           OR EXISTS(SELECT 1 FROM authors a JOIN book_authors ba ON ba.author_id=a.id WHERE ba.book_id=b.id AND lower(a.name) LIKE ?)
           OR EXISTS(SELECT 1 FROM tags t JOIN book_tags bt ON bt.tag_id=t.id WHERE bt.book_id=b.id AND lower(t.name) LIKE ?)
        LIMIT ?
    """, [f"%{q.lower()}%"]*4 + [limit]).fetchall()
    results = []
    for r in rows:
        results.append({
            "id": r["id"], "title": r["title"], "cover_color": r["cover_color"],
            "category": r["category"], "available": max(0, r["available"] or 0),
            "authors": get_book_authors(db, r["id"]),
        })
    return jsonify({"results": results})

# ══════════════════════════════════════════════════════════════════
#  STARTUP
# ══════════════════════════════════════════════════════════════════

with app.app_context():
    init_db()

if __name__=="__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)
