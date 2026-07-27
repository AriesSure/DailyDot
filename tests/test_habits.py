"""Habit endpoint tests — CRUD, check-in, uncheck, logs, write safety."""

from unittest.mock import patch


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


class TestWriteSafety:
    """DD-TASK-004: User confirmation, user_id binding, rollback, isolation."""

    def test_quick_create_binds_current_user(self, app, client, auth_headers):
        """POST /habits/new creates a Habit bound to the logged-in user."""
        from app.extensions import db
        from app.models import Habit, User
        current = db.session.query(User).filter_by(username="testuser").first()
        assert current is not None

        r = client.post("/habits/new", json={
            "habit_name": "MyHabit",
            "icon": "fas fa-star",
            "frequency": "Every day this week",
            "time_period": "Morning",
        }, headers=auth_headers)
        assert r.get_json()["success"] is True

        habit = db.session.query(Habit).filter_by(habit_name="MyHabit").first()
        assert habit is not None
        assert habit.user_id == current.id

    def test_parse_spoofed_user_id_discarded(self, app, client, auth_headers):
        """AI-parsed ``user_id`` is discarded; the created Habit uses the real user."""
        from app.extensions import db
        from app.models import Habit, User
        current = db.session.query(User).filter_by(username="testuser").first()

        # The form / UI never submits user_id from AI output;
        # the create route always uses current_user.id.
        # This test verifies that posting a spoofed user_id is ignored.
        r = client.post("/habits/new", json={
            "habit_name": "SafeHabit",
            "icon": "fas fa-star",
            "frequency": "Every day this week",
            "time_period": "Morning",
            "user_id": 999,  # ignored — habit_service uses current_user.id
        }, headers=auth_headers)
        assert r.get_json()["success"] is True

        habit = db.session.query(Habit).filter_by(habit_name="SafeHabit").first()
        assert habit.user_id == current.id
        assert habit.user_id != 999

    def test_quick_create_user_isolation(self, app, client, auth_headers):
        """User A's habit is not visible when logged in as User B (real session)."""
        from app.extensions import db
        from app.models import User, Habit

        # User A creates a unique habit via JSON Quick-Create
        response_a = client.post("/habits/new", json={
            "habit_name": "A-only habit",
            "icon": "fas fa-star",
            "frequency": "Every day this week",
            "time_period": "Morning",
            "note": "",
        }, headers=auth_headers)
        assert response_a.status_code == 200
        assert response_a.get_json()["success"] is True

        # Register and login User B
        client.get("/auth/logout", follow_redirects=True)
        client.post("/auth/register", data={
            "username": "user_b_iso",
            "email": "b_iso@test.com",
            "password": "Pass@1234",
            "confirm": "Pass@1234",
        }, follow_redirects=True)
        client.post("/auth/login", data={
            "username": "user_b_iso",
            "password": "Pass@1234",
        }, follow_redirects=True)
        headers_b = {"X-CSRFToken": "test", "Content-Type": "application/json"}

        # User B creates their own habit
        response_b = client.post("/habits/new", json={
            "habit_name": "B-only habit",
            "icon": "fas fa-star",
            "frequency": "Every day this week",
            "time_period": "Morning",
        }, headers=headers_b)
        assert response_b.status_code == 200
        assert response_b.get_json()["success"] is True

        # User B views the habit list page
        r = client.get("/habits/")
        assert r.status_code == 200
        body = r.get_data(as_text=True)
        assert "B-only habit" in body
        assert "A-only habit" not in body

    def test_habit_create_rollback(self, app, client, auth_headers):
        """When ``db.session.commit`` fails, rollback is called and no Habit is created."""
        from app.extensions import db
        from app.models import Habit

        with app.app_context():
            count_before = db.session.query(Habit).count()
            real_rollback = db.session.rollback

        with patch("app.services.habit_service.db.session.commit") as mock_commit:
            mock_commit.side_effect = RuntimeError("DB write failed")
            with patch("app.services.habit_service.db.session.rollback",
                       side_effect=lambda: real_rollback()) as mock_rollback:
                r = client.post("/habits/new", json={
                    "habit_name": "RollbackTest",
                    "icon": "fas fa-star",
                    "frequency": "Every day this week",
                    "time_period": "Morning",
                }, headers=auth_headers)
                assert r.get_json()["success"] is False
                mock_rollback.assert_called_once()

        with app.app_context():
            count_after = db.session.query(Habit).count()
        assert count_after == count_before
