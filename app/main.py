import logging
import os
from datetime import date

from flask import Flask, Response, flash, redirect, render_template, request, url_for
from sqlalchemy import inspect, text

from ._version import __version__
from .email_utils import EmailError, build_reminder_email, send_email
from .models import (
    CATEGORIES,
    FREQUENCY_TYPES,
    EmailSettings,
    Equipment,
    EquipmentGroup,
    MaintenanceTask,
    TaskLog,
    db,
    fmt_count,
)
from .scheduler import compute_next_due, init_scheduler, reschedule
from .tz import local_today, utc_now

log = logging.getLogger("maintenance_scheduler")


def migrate_schema(engine):
    """One-time, idempotent migration for databases created before Equipment
    Groups existed. Safe to run on every startup - it no-ops once the
    database is already up to date. Runs AFTER db.create_all() so brand-new
    tables (equipment_group) already exist by the time this runs."""
    inspector = inspect(engine)
    tables = inspector.get_table_names()
    if "equipment" not in tables or "maintenance_task" not in tables:
        return  # fresh database, nothing to migrate

    with engine.begin() as conn:
        eq_cols = {c["name"] for c in inspector.get_columns("equipment")}
        if "group_id" not in eq_cols:
            log.info("Migrating: adding equipment.group_id")
            conn.execute(text("ALTER TABLE equipment ADD COLUMN group_id INTEGER REFERENCES equipment_group(id)"))

        task_cols = {c["name"] for c in inspector.get_columns("maintenance_task")}
        if "group_id" not in task_cols:
            log.info("Migrating: rebuilding maintenance_task to support group_id and optional equipment_id")
            # SQLite can't ALTER a column's NOT NULL constraint directly, and
            # equipment_id must become nullable (group-targeted tasks have no
            # single equipment_id). Rebuild the table with the new schema and
            # copy existing rows across - every existing task keeps its
            # equipment_id and simply has group_id = NULL.
            conn.execute(text("""
                CREATE TABLE maintenance_task_new (
                    id INTEGER PRIMARY KEY,
                    equipment_id INTEGER REFERENCES equipment(id),
                    group_id INTEGER REFERENCES equipment_group(id),
                    title VARCHAR(150) NOT NULL,
                    description TEXT,
                    frequency_type VARCHAR(20) NOT NULL,
                    frequency_interval INTEGER,
                    next_due_date DATE NOT NULL,
                    reminder_days_before INTEGER NOT NULL,
                    notify_email VARCHAR(255),
                    active BOOLEAN NOT NULL,
                    last_sent_at DATETIME,
                    last_sent_for_due_date DATE,
                    created_at DATETIME
                )
            """))
            conn.execute(text("""
                INSERT INTO maintenance_task_new
                    (id, equipment_id, group_id, title, description, frequency_type,
                     frequency_interval, next_due_date, reminder_days_before, notify_email,
                     active, last_sent_at, last_sent_for_due_date, created_at)
                SELECT
                    id, equipment_id, NULL, title, description, frequency_type,
                    frequency_interval, next_due_date, reminder_days_before, notify_email,
                    active, last_sent_at, last_sent_for_due_date, created_at
                FROM maintenance_task
            """))
            conn.execute(text("DROP TABLE maintenance_task"))
            conn.execute(text("ALTER TABLE maintenance_task_new RENAME TO maintenance_task"))
            log.info("Migration complete: existing tasks kept their equipment, none were reassigned")


def create_app():
    app = Flask(__name__)
    app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-secret-change-me")

    db_path = os.environ.get("DATABASE_PATH", "/data/maintenance.db")
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{db_path}"
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

    db.init_app(app)

    with app.app_context():
        db.create_all()
        migrate_schema(db.engine)
        EmailSettings.get()  # ensure a settings row exists

    register_auth(app)
    register_routes(app)

    init_scheduler(app)

    log.info(f"Starting maintenance-scheduler v{__version__}")

    return app


def register_auth(app):
    user = os.environ.get("BASIC_AUTH_USER", "").strip()
    pw = os.environ.get("BASIC_AUTH_PASS", "").strip()
    if not user or not pw:
        return  # auth disabled

    @app.before_request
    def require_auth():
        auth = request.authorization
        if not auth or auth.username != user or auth.password != pw:
            return Response(
                "Authentication required.", 401,
                {"WWW-Authenticate": 'Basic realm="Maintenance Scheduler"'},
            )


def register_routes(app):

    @app.context_processor
    def inject_globals():
        return {"CATEGORIES": CATEGORIES, "FREQUENCY_TYPES": FREQUENCY_TYPES, "today": local_today()}

    @app.template_filter("fmt_count")
    def fmt_count_filter(n, singular):
        return fmt_count(n, singular)

    # ---------------- Dashboard ----------------
    @app.route("/")
    def dashboard():
        status_filter = request.args.get("status", "")
        category_filter = request.args.get("category", "")

        tasks = MaintenanceTask.query.order_by(MaintenanceTask.next_due_date.asc()).all()

        if category_filter:
            tasks = [t for t in tasks if t.target_category == category_filter]

        if status_filter:
            tasks = [t for t in tasks if t.status == status_filter]
        else:
            # Default view hides completed tasks so they don't clutter the
            # dashboard; pick "Completed" in the status filter to see them.
            tasks = [t for t in tasks if t.status != "completed"]

        counts = {"overdue": 0, "due_soon": 0, "scheduled": 0, "completed": 0}
        for t in MaintenanceTask.query.all():
            counts[t.status] = counts.get(t.status, 0) + 1

        return render_template(
            "index.html", tasks=tasks, counts=counts,
            status_filter=status_filter, category_filter=category_filter,
        )

    # ---------------- Equipment ----------------
    @app.route("/equipment")
    def equipment_list():
        groups = EquipmentGroup.query.order_by(EquipmentGroup.name).all()
        ungrouped = Equipment.query.filter_by(group_id=None).order_by(Equipment.name).all()
        return render_template("equipment.html", groups=groups, ungrouped=ungrouped)

    @app.route("/equipment/new", methods=["GET", "POST"])
    def equipment_new():
        groups = EquipmentGroup.query.order_by(EquipmentGroup.name).all()
        if request.method == "POST":
            group_id = request.form.get("group_id") or None
            eq = Equipment(
                name=request.form["name"].strip(),
                category=request.form.get("category", "Other"),
                location=request.form.get("location", "").strip(),
                notes=request.form.get("notes", "").strip(),
                group_id=int(group_id) if group_id else None,
            )
            db.session.add(eq)
            db.session.commit()
            flash(f"Added equipment '{eq.name}'.", "success")
            return redirect(url_for("equipment_list"))
        return render_template("equipment_form.html", item=None, groups=groups)

    @app.route("/equipment/<int:eq_id>/edit", methods=["GET", "POST"])
    def equipment_edit(eq_id):
        eq = Equipment.query.get_or_404(eq_id)
        groups = EquipmentGroup.query.order_by(EquipmentGroup.name).all()
        if request.method == "POST":
            group_id = request.form.get("group_id") or None
            eq.name = request.form["name"].strip()
            eq.category = request.form.get("category", "Other")
            eq.location = request.form.get("location", "").strip()
            eq.notes = request.form.get("notes", "").strip()
            eq.group_id = int(group_id) if group_id else None
            db.session.commit()
            flash(f"Updated '{eq.name}'.", "success")
            return redirect(url_for("equipment_list"))
        return render_template("equipment_form.html", item=eq, groups=groups)

    @app.route("/equipment/<int:eq_id>/delete", methods=["POST"])
    def equipment_delete(eq_id):
        eq = Equipment.query.get_or_404(eq_id)
        name = eq.name
        db.session.delete(eq)
        db.session.commit()
        flash(f"Deleted '{name}' and its tasks.", "success")
        return redirect(url_for("equipment_list"))

    # ---------------- Equipment Groups ----------------
    @app.route("/groups")
    def group_list():
        groups = EquipmentGroup.query.order_by(EquipmentGroup.name).all()
        return render_template("groups.html", groups=groups)

    @app.route("/groups/new", methods=["GET", "POST"])
    def group_new():
        if request.method == "POST":
            group = EquipmentGroup(
                name=request.form["name"].strip(),
                category=request.form.get("category") or None,
                description=request.form.get("description", "").strip(),
            )
            db.session.add(group)
            db.session.commit()
            flash(f"Created group '{group.name}'.", "success")
            return redirect(url_for("group_list"))
        return render_template("group_form.html", group=None)

    @app.route("/groups/<int:group_id>/edit", methods=["GET", "POST"])
    def group_edit(group_id):
        group = EquipmentGroup.query.get_or_404(group_id)
        if request.method == "POST":
            group.name = request.form["name"].strip()
            group.category = request.form.get("category") or None
            group.description = request.form.get("description", "").strip()
            db.session.commit()
            flash(f"Updated group '{group.name}'.", "success")
            return redirect(url_for("group_list"))
        return render_template("group_form.html", group=group)

    @app.route("/groups/<int:group_id>/delete", methods=["POST"])
    def group_delete(group_id):
        group = EquipmentGroup.query.get_or_404(group_id)
        name = group.name
        # Un-group its equipment (don't delete the equipment itself);
        # tasks assigned to the group are removed via cascade.
        for eq in list(group.equipment):
            eq.group_id = None
        db.session.delete(group)
        db.session.commit()
        flash(f"Deleted group '{name}'. Its equipment was kept but ungrouped.", "success")
        return redirect(url_for("group_list"))

    # ---------------- Tasks ----------------
    def _parse_target(form):
        """Parses the combined 'target' field, e.g. 'equipment:5' or 'group:2',
        into (equipment_id, group_id)."""
        raw = form.get("target", "")
        kind, _, raw_id = raw.partition(":")
        if kind == "group" and raw_id:
            return None, int(raw_id)
        if kind == "equipment" and raw_id:
            return int(raw_id), None
        return None, None

    @app.route("/tasks/new", methods=["GET", "POST"])
    def task_new():
        equipment_items = Equipment.query.order_by(Equipment.name).all()
        groups = EquipmentGroup.query.order_by(EquipmentGroup.name).all()
        if not equipment_items and not groups:
            flash("Add a piece of equipment (or an equipment group) first.", "warning")
            return redirect(url_for("equipment_new"))

        if request.method == "POST":
            equipment_id, group_id = _parse_target(request.form)
            if not equipment_id and not group_id:
                flash("Choose what this task applies to.", "danger")
                return render_template("task_form.html", task=None, equipment_items=equipment_items, groups=groups)

            task = MaintenanceTask(
                equipment_id=equipment_id,
                group_id=group_id,
                title=request.form["title"].strip(),
                description=request.form.get("description", "").strip(),
                frequency_type=request.form.get("frequency_type", "once"),
                frequency_interval=int(request.form.get("frequency_interval") or 1),
                next_due_date=date.fromisoformat(request.form["next_due_date"]),
                reminder_days_before=int(request.form.get("reminder_days_before") or 3),
                notify_email=request.form.get("notify_email", "").strip() or None,
            )
            # Look up the target name from the lists already in hand, rather
            # than task.target_name, since the new task's relationships
            # aren't resolvable until after a flush/commit.
            if group_id:
                target_label = next((g.name for g in groups if g.id == group_id), "group")
            else:
                target_label = next((e.name for e in equipment_items if e.id == equipment_id), "equipment")

            db.session.add(task)
            db.session.add(TaskLog(task=task, due_date=task.next_due_date, event="created",
                                    note=f"Task created for {target_label}"))
            db.session.commit()
            flash(f"Created task '{task.title}'.", "success")
            return redirect(url_for("dashboard"))

        return render_template("task_form.html", task=None, equipment_items=equipment_items, groups=groups)

    @app.route("/tasks/<int:task_id>/edit", methods=["GET", "POST"])
    def task_edit(task_id):
        task = MaintenanceTask.query.get_or_404(task_id)
        equipment_items = Equipment.query.order_by(Equipment.name).all()
        groups = EquipmentGroup.query.order_by(EquipmentGroup.name).all()

        if request.method == "POST":
            equipment_id, group_id = _parse_target(request.form)
            if not equipment_id and not group_id:
                flash("Choose what this task applies to.", "danger")
                return render_template("task_form.html", task=task, equipment_items=equipment_items, groups=groups)

            task.equipment_id = equipment_id
            task.group_id = group_id
            task.title = request.form["title"].strip()
            task.description = request.form.get("description", "").strip()
            task.frequency_type = request.form.get("frequency_type", "once")
            task.frequency_interval = int(request.form.get("frequency_interval") or 1)
            task.next_due_date = date.fromisoformat(request.form["next_due_date"])
            task.reminder_days_before = int(request.form.get("reminder_days_before") or 3)
            task.notify_email = request.form.get("notify_email", "").strip() or None
            task.active = "active" in request.form
            db.session.commit()
            flash(f"Updated task '{task.title}'.", "success")
            return redirect(url_for("dashboard"))

        return render_template("task_form.html", task=task, equipment_items=equipment_items, groups=groups)

    @app.route("/tasks/<int:task_id>/delete", methods=["POST"])
    def task_delete(task_id):
        task = MaintenanceTask.query.get_or_404(task_id)
        title = task.title
        db.session.delete(task)
        db.session.commit()
        flash(f"Deleted task '{title}'.", "success")
        return redirect(url_for("dashboard"))

    @app.route("/tasks/<int:task_id>/complete", methods=["POST"])
    def task_complete(task_id):
        task = MaintenanceTask.query.get_or_404(task_id)
        old_due = task.next_due_date

        db.session.add(TaskLog(task_id=task.id, due_date=old_due, event="completed"))

        if task.is_recurring:
            task.next_due_date = compute_next_due(old_due, task.frequency_type, task.frequency_interval)
            task.last_sent_for_due_date = None
        else:
            task.active = False

        db.session.commit()
        flash(f"Marked '{task.title}' complete.", "success")
        return redirect(url_for("dashboard"))

    @app.route("/tasks/<int:task_id>/send-now", methods=["POST"])
    def task_send_now(task_id):
        task = MaintenanceTask.query.get_or_404(task_id)
        settings = EmailSettings.get()
        to_addr = task.notify_email or settings.default_to_email
        subject, text, html = build_reminder_email(task)
        try:
            send_email(settings, to_addr, subject, text, html)
            task.last_sent_at = utc_now()
            task.last_sent_for_due_date = task.next_due_date
            db.session.add(TaskLog(task_id=task.id, due_date=task.next_due_date,
                                    event="reminder_sent", note=f"Manually sent to {to_addr}"))
            db.session.commit()
            flash(f"Reminder email sent to {to_addr}.", "success")
        except EmailError as exc:
            db.session.add(TaskLog(task_id=task.id, due_date=task.next_due_date,
                                    event="reminder_failed", note=str(exc)))
            db.session.commit()
            flash(str(exc), "danger")
        return redirect(url_for("dashboard"))

    @app.route("/tasks/<int:task_id>/log")
    def task_log(task_id):
        task = MaintenanceTask.query.get_or_404(task_id)
        return render_template("task_log.html", task=task)

    # ---------------- Settings ----------------
    @app.route("/settings", methods=["GET", "POST"])
    def settings_page():
        settings = EmailSettings.get()
        if request.method == "POST":
            settings.smtp_host = request.form.get("smtp_host", "").strip()
            settings.smtp_port = int(request.form.get("smtp_port") or 587)
            settings.smtp_username = request.form.get("smtp_username", "").strip()
            new_pw = request.form.get("smtp_password", "")
            if new_pw:  # keep existing password if left blank
                settings.smtp_password = new_pw
            settings.use_tls = "use_tls" in request.form
            settings.use_ssl = "use_ssl" in request.form
            settings.from_email = request.form.get("from_email", "").strip()
            settings.default_to_email = request.form.get("default_to_email", "").strip()
            settings.check_interval_minutes = int(request.form.get("check_interval_minutes") or 60)
            db.session.commit()
            reschedule(app)
            flash("Email settings saved.", "success")
            return redirect(url_for("settings_page"))
        return render_template("settings.html", settings=settings)

    @app.route("/settings/test", methods=["POST"])
    def settings_test():
        settings = EmailSettings.get()
        to_addr = request.form.get("test_to") or settings.default_to_email
        try:
            send_email(
                settings, to_addr,
                "Maintenance Scheduler - Test Email",
                "This is a test email from your Maintenance Scheduler app. "
                "If you received this, your SMTP settings are working.",
            )
            flash(f"Test email sent to {to_addr}.", "success")
        except EmailError as exc:
            flash(str(exc), "danger")
        return redirect(url_for("settings_page"))


app = create_app()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
