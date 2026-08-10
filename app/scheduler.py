import logging

from apscheduler.schedulers.background import BackgroundScheduler
from dateutil.relativedelta import relativedelta

from .email_utils import EmailError, build_reminder_email, send_email
from .models import EmailSettings, MaintenanceTask, TaskLog, db
from .tz import local_today

log = logging.getLogger("maintenance_scheduler")

_scheduler = None


def compute_next_due(current_due, frequency_type, interval):
    interval = interval or 1
    if frequency_type == "days":
        return current_due + relativedelta(days=interval)
    if frequency_type == "weeks":
        return current_due + relativedelta(weeks=interval)
    if frequency_type == "months":
        return current_due + relativedelta(months=interval)
    if frequency_type == "years":
        return current_due + relativedelta(years=interval)
    return current_due  # 'once' - no advance


def advance_if_overdue_recurring(task):
    """If a recurring task's due date has slipped into the past with no action,
    keep rolling it forward so reminders reflect the *next* real occurrence
    once it's badly overdue (more than one full cycle late)."""
    # We intentionally do NOT auto-skip overdue occurrences by default -
    # overdue should stay visible as overdue until the user marks it complete.
    return task


def check_and_send_reminders(app):
    """Runs periodically. Sends reminder emails for tasks entering their
    reminder window, and logs the send so we don't email twice for the
    same due-date occurrence."""
    with app.app_context():
        settings = EmailSettings.get()
        today = local_today()

        tasks = MaintenanceTask.query.filter_by(active=True).all()
        for task in tasks:
            days_until = (task.next_due_date - today).days
            already_sent = task.last_sent_for_due_date == task.next_due_date

            should_send = days_until <= (task.reminder_days_before or 0) and not already_sent

            if not should_send:
                continue

            to_addr = task.notify_email or settings.default_to_email
            subject, text, html = build_reminder_email(task)

            try:
                send_email(settings, to_addr, subject, text, html)
                task.last_sent_at = db.func.now()
                task.last_sent_for_due_date = task.next_due_date
                db.session.add(TaskLog(task_id=task.id, due_date=task.next_due_date,
                                        event="reminder_sent", note=f"Sent to {to_addr}"))
                db.session.commit()
                log.info("Sent reminder for task %s (%s) to %s", task.id, task.title, to_addr)
            except EmailError as exc:
                db.session.add(TaskLog(task_id=task.id, due_date=task.next_due_date,
                                        event="reminder_failed", note=str(exc)))
                db.session.commit()
                log.warning("Failed to send reminder for task %s: %s", task.id, exc)


def init_scheduler(app):
    global _scheduler
    if _scheduler is not None:
        return _scheduler

    settings = None
    with app.app_context():
        settings = EmailSettings.get()

    interval = max(5, settings.check_interval_minutes or 60)

    scheduler = BackgroundScheduler(daemon=True)
    scheduler.add_job(
        func=lambda: check_and_send_reminders(app),
        trigger="interval",
        minutes=interval,
        id="reminder_check",
        next_run_time=None,  # first run scheduled below after short delay
        replace_existing=True,
    )
    scheduler.start()
    _scheduler = scheduler

    # Run an initial check shortly after startup
    scheduler.add_job(
        func=lambda: check_and_send_reminders(app),
        trigger="date",
        id="reminder_check_initial",
        misfire_grace_time=None,
    )

    return scheduler


def reschedule(app):
    """Call after the user changes the check interval in Settings."""
    if _scheduler is None:
        return
    with app.app_context():
        settings = EmailSettings.get()
    interval = max(5, settings.check_interval_minutes or 60)
    _scheduler.reschedule_job("reminder_check", trigger="interval", minutes=interval)