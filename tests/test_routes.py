import re


def _csrf(client, path):
    html = client.get(path).get_data(as_text=True)
    return re.search(r'name="_csrf_token" value="([^"]+)"', html).group(1)


def _login(client, username="admin", password="Admin@123"):
    token = _csrf(client, "/login")
    return client.post(
        "/login", data={"username": username, "password": password, "_csrf_token": token}
    )


def test_index_redirects_to_login(client):
    response = client.get("/")
    assert response.status_code == 302
    assert "/login" in response.headers["Location"]


def test_login_page_loads(client):
    response = client.get("/login")
    assert response.status_code == 200
    assert b"Sign In" in response.data


def test_protected_pages_require_login(client):
    for path in ("/dashboard", "/books", "/fines", "/profile", "/admin"):
        response = client.get(path)
        assert response.status_code == 302
        assert "/login" in response.headers["Location"]


def test_login_rejects_missing_csrf_token(client):
    response = client.post("/login", data={"username": "admin", "password": "Admin@123"})
    assert response.status_code == 302
    with client.session_transaction() as sess:
        assert "uid" not in sess


def test_login_with_wrong_password_fails(client):
    response = _login(client, password="wrong")
    assert response.status_code == 200
    assert b"Invalid username or password" in response.data


def test_admin_login_and_admin_panel(client):
    response = _login(client)
    assert response.status_code == 302
    assert client.get("/admin").status_code == 200


def test_signup_then_login(client):
    token = _csrf(client, "/signup")
    response = client.post(
        "/signup",
        data={"username": "reader1", "password": "Reader@123", "_csrf_token": token},
    )
    assert response.status_code == 302
    assert _login(client, "reader1", "Reader@123").status_code == 302
    assert client.get("/admin").status_code == 403


def test_signup_rejects_weak_password(client):
    token = _csrf(client, "/signup")
    response = client.post(
        "/signup", data={"username": "reader2", "password": "short", "_csrf_token": token}
    )
    assert b"at least 8 characters" in response.data


def test_search_is_parameterised(client):
    _login(client)
    response = client.get("/books?q=' OR 1=1 --")
    assert response.status_code == 200
    assert b"No books match" in response.data


def test_issue_and_return_book(client):
    _login(client)
    assert client.get("/issue/1").status_code == 302
    page = client.get("/dashboard").get_data(as_text=True)
    assert "Artificial Intelligence" in page
    txn = re.search(r"/return/(\d+)", page).group(1)
    assert client.get(f"/return/{txn}").status_code == 302
    assert not re.search(r"/return/\d+", client.get("/dashboard").get_data(as_text=True))


def test_api_autocomplete_returns_json(client):
    response = client.get("/api/autocomplete?q=test")
    assert response.status_code == 200
    assert response.is_json
