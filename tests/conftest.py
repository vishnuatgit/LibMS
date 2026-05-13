import pytest
import os
import tempfile
import sqlite3
from app import app, init_db

@pytest.fixture
def client():
    # Create a temporary file to isolate the database for each test
    db_fd, app.config['DATABASE'] = tempfile.mkstemp()
    app.config['TESTING'] = True
    
    with app.test_client() as client:
        with app.app_context():
            init_db()
        yield client
    
    os.close(db_fd)
    os.unlink(app.config['DATABASE'])
