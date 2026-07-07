"""Todo endpoint tests — CRUD and completion toggle."""


class TestTodoCRUD:
    def test_create_todo(self, client, auth_headers):
        r = client.post('/todos/new', json={
            'event': 'Buy milk', 'date': '08/07/2026', 'time': '10:00',
        }, headers=auth_headers)
        assert r.status_code == 200
        assert r.get_json()['success'] is True

    def test_list_todos(self, client, auth_headers):
        r = client.get('/todos/list')
        assert r.status_code == 200

    def test_complete_todo(self, client, auth_headers):
        client.post('/todos/new', json={
            'event': 'Task 1', 'date': '08/07/2026', 'time': '12:00',
        }, headers=auth_headers)
        r = client.post('/todos/complete/1', json={}, headers=auth_headers)
        assert r.get_json()['success'] is True

    def test_toggle_complete(self, client, auth_headers):
        client.post('/todos/new', json={
            'event': 'Task 2', 'date': '08/07/2026', 'time': '14:00',
        }, headers=auth_headers)
        client.post('/todos/complete/1', json={}, headers=auth_headers)
        r = client.post('/todos/complete/1', json={}, headers=auth_headers)
        assert r.get_json()['success'] is True

    def test_edit_todo(self, client, auth_headers):
        client.post('/todos/new', json={
            'event': 'Original', 'date': '08/07/2026', 'time': '09:00',
        }, headers=auth_headers)
        r = client.post('/todos/edit/1', json={
            'event': 'Updated', 'date': '09/07/2026', 'time': '10:00',
        }, headers=auth_headers)
        assert r.get_json()['success'] is True

    def test_delete_todo(self, client, auth_headers):
        client.post('/todos/new', json={
            'event': 'Delete me', 'date': '08/07/2026', 'time': '11:00',
        }, headers=auth_headers)
        r = client.post('/todos/delete/1', json={}, headers=auth_headers)
        assert r.get_json()['success'] is True

    def test_todo_not_found(self, client, auth_headers):
        r = client.post('/todos/complete/999', json={}, headers=auth_headers)
        assert r.get_json()['success'] is False
