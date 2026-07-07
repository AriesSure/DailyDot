"""Auth endpoint tests — register, login, logout, change password."""


class TestRegister:
    def test_success(self, client):
        r = client.post('/auth/register', data={
            'username': 'newuser',
            'email': 'new@example.com',
            'password': 'Strong@123',
            'confirm': 'Strong@123',
        }, follow_redirects=True)
        assert r.status_code == 200

    def test_duplicate_username(self, client):
        client.post('/auth/register', data={
            'username': 'dup', 'email': 'd1@e.com',
            'password': 'Strong@123', 'confirm': 'Strong@123',
        })
        r = client.post('/auth/register', data={
            'username': 'dup', 'email': 'd2@e.com',
            'password': 'Strong@123', 'confirm': 'Strong@123',
        }, follow_redirects=True)
        assert r.status_code == 200

    def test_password_mismatch(self, client):
        r = client.post('/auth/register', data={
            'username': 'user', 'email': 'u@e.com',
            'password': 'Strong@123', 'confirm': 'Different@456',
        }, follow_redirects=True)
        assert r.status_code == 200


class TestLogin:
    def test_success(self, client):
        client.post('/auth/register', data={
            'username': 'logintest', 'email': 'l@e.com',
            'password': 'Pass@1234', 'confirm': 'Pass@1234',
        })
        r = client.post('/auth/login', data={
            'username': 'logintest',
            'password': 'Pass@1234',
            'remember_me': 'y',
        }, follow_redirects=True)
        assert r.status_code == 200

    def test_wrong_password(self, client):
        client.post('/auth/register', data={
            'username': 'failtest', 'email': 'f@e.com',
            'password': 'Correct@1', 'confirm': 'Correct@1',
        })
        r = client.post('/auth/login', data={
            'username': 'failtest',
            'password': 'Wrong@1',
        }, follow_redirects=True)
        assert r.status_code == 200


class TestLogout:
    def test_logout(self, client):
        client.post('/auth/register', data={
            'username': 'logoutuser', 'email': 'lo@e.com',
            'password': 'Pass@1234', 'confirm': 'Pass@1234',
        })
        client.post('/auth/login', data={
            'username': 'logoutuser', 'password': 'Pass@1234', 'remember_me': 'y',
        })
        r = client.get('/auth/logout', follow_redirects=True)
        assert r.status_code == 200


class TestChangePassword:
    def test_change_pw_page(self, client, auth_headers):
        r = client.get('/auth/change_pw')
        assert r.status_code == 200
