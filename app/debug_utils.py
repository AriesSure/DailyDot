from app import app, db
from app.models import User, Habit, Record, Todo, Card
from flask_migrate import upgrade, init, migrate
from flask import current_app
import os

def reset_db():
    """Reset database - only use for development/testing"""
    with app.app_context():
        try:
            db.drop_all()
            db.create_all()

            u1 = User(username='amy', email='a@b.com' )
            u1.set_password('amy.pw')
            u2 = User(username='tom', email='t@b.com')
            u2.set_password('tom.pw')
            u3 = User(username='yin', email='y@b.com')
            u3.set_password('yin.pw')
            u4 = User(username='tariq', email='tariq@b.com')
            u4.set_password('tariq.pw')
            u5 = User(username='jo', email='jo@b.com')
            u5.set_password('jo.pw')

            u1.habits.append(Habit(habit_name='Get up early', icon='fas fa-sun', frequency='Every day this week', time_period='After waking up', note=None))
            u1.habits.append(Habit(habit_name='Go to bed early', icon='fas fa-bed', frequency='Every day this week', time_period='Before bedtime', note=None))

            u2.habits.append(Habit(habit_name='Jogging', icon='fas fa-running', frequency='4 times a week', time_period='Morning', note=None))

            u3.habits.append(Habit(habit_name='Swimming', icon='fas fa-swimmer', frequency='2 times a week', time_period='Afternoon', note=None))

            u4.habits.append(Habit(habit_name='Reading books', icon='fas fa-book', frequency='Every day this week', time_period='Evening', note=None))

            u5.habits.append(Habit(habit_name='Gym', icon='fas fa-dumbbell', frequency='3 times a week', time_period='Noon', note=None))

            db.session.add_all([u1, u2, u3, u4, u5])
            db.session.commit()
            print("Database reset successfully")

        except Exception as e:
            db.session.rollback()
            print(f"Error during commit: {str(e)}")

def init_migrations():
    """Initialize database migrations - run this once to set up migrations"""
    with app.app_context():
        try:
            # Check if migrations directory exists
            if not os.path.exists('migrations'):
                init()
                print("Migrations initialized successfully")
            else:
                print("Migrations already initialized")
        except Exception as e:
            print(f"Error initializing migrations: {str(e)}")

def upgrade_db():
    """Apply all pending migrations to update database structure"""
    with app.app_context():
        try:
            upgrade()
            print("Database upgraded successfully")
        except Exception as e:
            print(f"Error upgrading database: {str(e)}")

def create_migration(message):
    """Create a new migration with the given message"""
    with app.app_context():
        try:
            migrate(message=message)
            print(f"Migration '{message}' created successfully")
        except Exception as e:
            print(f"Error creating migration: {str(e)}")

