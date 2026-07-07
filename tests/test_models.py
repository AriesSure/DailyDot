"""Model-level tests — default values, relationships, repr."""

from datetime import date, datetime

from app.extensions import db
from app.models import User, Habit, Record, Todo, Card


class TestUser:
    def test_create(self, app):
        with app.app_context():
            u = User(username='alice', email='a@b.com')
            u.set_password('Secret@123')
            db.session.add(u)
            db.session.commit()
            assert u.id is not None
            assert u.check_password('Secret@123') is True
            assert u.check_password('wrong') is False

    def test_repr(self, app):
        with app.app_context():
            u = User(username='bob', email='b@b.com')
            u.set_password('Xyz@7890')
            assert 'User(id=' in repr(u)


class TestTodo:
    def test_create(self, app):
        with app.app_context():
            t = Todo(event='Test', date='01/01/2025', time='08:00', user_id=1)
            db.session.add(t)
            db.session.commit()
            assert t.completed is False
            assert t.created_at is not None

    def test_repr(self, app):
        with app.app_context():
            t = Todo(event='Test', date='01/01/2025', time='08:00', user_id=1)
            # Must say "Todo(" not "Habit("
            assert 'Todo(' in repr(t), f'Bad repr: {repr(t)}'


class TestHabit:
    def test_create(self, app):
        with app.app_context():
            h = Habit(
                habit_name='Reading',
                icon='fas fa-book',
                frequency='Every day',
                time_period='Evening',
                user_id=1,
            )
            db.session.add(h)
            db.session.commit()
            assert h.id is not None


class TestRecord:
    def test_default_checkin_time(self, app):
        with app.app_context():
            col = Record.__table__.columns['checkin_time']
            # Must be a CallableColumnDefault (lambda), not a fixed time
            assert col.default.is_callable, 'checkin_time default should be callable'

    def test_create(self, app):
        with app.app_context():
            r = Record(habit_id=1, note='Test')
            db.session.add(r)
            db.session.commit()
            assert r.checkin_date == date.today()
            assert r.checkin_time is not None

    def test_unique_constraint(self, app):
        with app.app_context():
            r1 = Record(habit_id=1, checkin_date=date.today(), checkin_time=datetime.now().time())
            db.session.add(r1)
            db.session.commit()
            r2 = Record(habit_id=1, checkin_date=date.today(), checkin_time=datetime.now().time())
            db.session.add(r2)
            import pytest
            with pytest.raises(Exception):
                db.session.commit()
            db.session.rollback()


class TestCard:
    def test_create(self, app):
        with app.app_context():
            c = Card(user_id=1, date=date.today(), quote='Test')
            db.session.add(c)
            db.session.commit()
            assert c.streak == 0
            assert c.habits_completed == 0
