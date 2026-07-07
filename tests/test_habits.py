"""Habit endpoint tests — CRUD, check-in, uncheck, logs."""


class TestHabitCRUD:
    def test_create_habit(self, client, auth_headers):
        r = client.post('/habits/new', json={
            'habit_name': 'Read Books',
            'icon': 'fas fa-book',
            'frequency': 'Every day this week',
            'time_period': 'Evening',
        }, headers=auth_headers)
        assert r.status_code == 200
        data = r.get_json()
        assert data['success'] is True

    def test_create_duplicate(self, client, auth_headers):
        client.post('/habits/new', json={
            'habit_name': 'Duplicate', 'icon': 'fas fa-star',
            'frequency': 'Once a week', 'time_period': 'Morning',
        }, headers=auth_headers)
        r = client.post('/habits/new', json={
            'habit_name': 'Duplicate', 'icon': 'fas fa-star',
            'frequency': 'Once a week', 'time_period': 'Morning',
        }, headers=auth_headers)
        assert r.get_json()['success'] is False

    def test_list_habits(self, client, auth_headers):
        client.post('/habits/new', json={
            'habit_name': 'Jog', 'icon': 'fas fa-running',
            'frequency': '3 times a week', 'time_period': 'Morning',
        }, headers=auth_headers)
        r = client.get('/habits/')
        assert r.status_code == 200

    def test_view_habit(self, client, auth_headers):
        client.post('/habits/new', json={
            'habit_name': 'ViewTest', 'icon': 'fas fa-eye',
            'frequency': 'Once a week', 'time_period': 'Noon',
        }, headers=auth_headers)
        r = client.get('/habits/view/1')
        assert r.status_code == 200

    def test_delete_habit(self, client, auth_headers):
        client.post('/habits/new', json={
            'habit_name': 'DeleteMe', 'icon': 'fas fa-trash',
            'frequency': 'Once a week', 'time_period': 'Morning',
        }, headers=auth_headers)
        r = client.post('/habits/delete/1', headers=auth_headers)
        assert r.get_json()['success'] is True


class TestCheckin:
    def test_check_in(self, client, auth_headers):
        client.post('/habits/new', json={
            'habit_name': 'Run', 'icon': 'fas fa-running',
            'frequency': 'Every day', 'time_period': 'Morning',
        }, headers=auth_headers)
        r = client.post('/habits/check_in/1', json={'time': '07:30', 'note': 'Great!'}, headers=auth_headers)
        assert r.get_json()['success'] is True

    def test_double_checkin_fails(self, client, auth_headers):
        client.post('/habits/new', json={
            'habit_name': 'Run2', 'icon': 'fas fa-running',
            'frequency': 'Every day', 'time_period': 'Morning',
        }, headers=auth_headers)
        client.post('/habits/check_in/1', json={'time': '07:30'}, headers=auth_headers)
        r = client.post('/habits/check_in/1', json={'time': '08:00'}, headers=auth_headers)
        assert r.get_json()['success'] is False

    def test_checkin_by_date(self, client, auth_headers):
        client.post('/habits/new', json={
            'habit_name': 'Swim', 'icon': 'fas fa-swimmer',
            'frequency': '2 times a week', 'time_period': 'Afternoon',
        }, headers=auth_headers)
        r = client.post('/habits/checkin_by_date/1', json={
            'date': '2026-07-01', 'time': '14:00', 'note': 'Pool',
        }, headers=auth_headers)
        assert r.get_json()['success'] is True

    def test_uncheck_by_date(self, client, auth_headers):
        client.post('/habits/new', json={
            'habit_name': 'Yoga', 'icon': 'fas fa-pray',
            'frequency': '4 times a week', 'time_period': 'Morning',
        }, headers=auth_headers)
        client.post('/habits/checkin_by_date/1', json={
            'date': '2026-07-01', 'time': '08:00',
        }, headers=auth_headers)
        r = client.post('/habits/uncheck_by_date/1', json={'date': '2026-07-01'}, headers=auth_headers)
        assert r.get_json()['success'] is True

    def test_checkin_logs(self, client, auth_headers):
        client.post('/habits/new', json={
            'habit_name': 'LogTest', 'icon': 'fas fa-pen',
            'frequency': 'Every day', 'time_period': 'Evening',
        }, headers=auth_headers)
        client.post('/habits/check_in/1', json={'time': '20:00', 'note': 'Done'}, headers=auth_headers)
        r = client.get('/habits/checkin_logs/1')
        assert r.status_code == 200
