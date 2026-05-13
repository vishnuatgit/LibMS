import pytest

def test_index_redirect(client):
    """Test that the index redirects to login if not authenticated."""
    response = client.get('/')
    assert response.status_code == 302
    assert b'/login' in response.data

def test_login_page_loads(client):
    """Test that the login page loads correctly."""
    response = client.get('/login')
    assert response.status_code == 200
    assert b'Sign In' in response.data

def test_api_autocomplete(client):
    """Test the autocomplete API endpoint."""
    response = client.get('/api/autocomplete?q=test')
    assert response.status_code == 200
    assert response.is_json
