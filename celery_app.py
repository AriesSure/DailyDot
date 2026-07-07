"""Celery worker configuration (optional — for scheduled AI reports).

Usage:
    celery -A celery_app worker --loglevel=info
    celery -A celery_app beat --loglevel=info   # enable scheduled tasks
"""

import os

from celery import Celery
from celery.schedules import crontab

# Ensure Flask app is importable for database access within tasks
os.environ.setdefault("FLASK_ENV", "production")

REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")

celery = Celery("dailydot", broker=REDIS_URL, backend=REDIS_URL)
celery.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="Asia/Shanghai",
    enable_utc=False,
)


@celery.on_after_configure.connect
def setup_periodic_tasks(sender, **kwargs):
    """Schedule weekly AI reports — every Monday at 9:00 AM."""
    sender.add_periodic_task(
        crontab(hour=9, minute=0, day_of_week=1),
        generate_weekly_reports.s(),
        name="Generate weekly AI reports",
    )


@celery.task
def generate_weekly_reports():
    """Generate weekly AI reports for all active users."""
    from app import create_app
    from app.extensions import db
    from app.models import User

    app = create_app()
    with app.app_context():
        users = db.session.query(User).all()
        for user in users:
            _generate_report.delay(user.id)


@celery.task
def _generate_report(user_id):
    """Generate and optionally persist an AI report for a single user."""
    try:
        from app import create_app
        from app.extensions import db
        from app.ai.llm_client import get_llm
        from app.ai.prompt_templates import report_prompt
        from app.services.statistics_service import get_habit_stats, get_daily_checkin_data, get_todo_stats
        from app.models import Habit, Record
        from datetime import date, timedelta
        import markdown

        app = create_app()
        with app.app_context():
            llm = get_llm()
            if not llm.available:
                return

            start = date.today() - timedelta(days=7)
            habits = db.session.query(Habit).filter_by(user_id=user_id).all()
            total_checkins = db.session.query(Record).join(Habit).filter(
                Habit.user_id == user_id, Record.checkin_date >= start
            ).count()

            stats = {
                "period": f"{start.isoformat()} to {date.today().isoformat()}",
                "total_habits": len(habits),
                "total_checkins": total_checkins,
                "completion_rate": 0,
                "streak": 0,
                "missed_days": 0,
                "top_habit": get_habit_stats(user_id)[0]["name"] if get_habit_stats(user_id) else "N/A",
            }

            messages = report_prompt(stats, period="weekly", tone="coach")
            report_md = llm.chat(messages, temperature=0.7)
            _ = markdown.markdown(report_md)
            # In production: persist to a Report model or send via email
    except Exception:
        pass
