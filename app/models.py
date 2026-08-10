from datetime import date, datetime

from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()

CATEGORIES = ["Server", "Appliance", "Tool", "Vehicle", "HVAC", "Other"]
FREQUENCY_TYPES = ["once", "days", "weeks", "months", "years"]


def pluralize(singular, n):
    """Return the singular or plural form of a word based on n, e.g.
    pluralize('day', 1) -> 'day', pluralize('day', 3) -> 'days'."""
    n = abs(n)
    if n == 1:
        return singular
    if singular.endswith("y") and singular[-2:-1] not in "aeiou":
        return singular[:-1] + "ies"
    return singular + "s"


def fmt_count(n, singular):
    """e.g. fmt_count(1, 'day') -> '1 day', fmt_count(3, 'day') -> '3 days'."""
    return f"{n} {pluralize(singular, n)}"


class EquipmentGroup(db.Model):
    __tablename__ = "equipment_group"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    category = db.Column(db.String(50))  # optional, used for dashboard filtering
    description = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    # Equipment items belonging to this group. Deleting a group does NOT
    # delete the equipment in it - it just un-groups them (see route logic).
    equipment = db.relationship(
        "Equipment", backref="group", order_by="Equipment.name"
    )

    # Tasks assigned directly to the group (applies to every item in it).
    # These ARE deleted if the group is deleted.
    tasks = db.relationship(
        "MaintenanceTask",
        backref="group",
        cascade="all, delete-orphan",
        order_by="MaintenanceTask.next_due_date",
        foreign_keys="MaintenanceTask.group_id",
    )

    @property
    def item_count(self):
        return len(self.equipment)


class Equipment(db.Model):
    __tablename__ = "equipment"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    category = db.Column(db.String(50), nullable=False, default="Other")
    location = db.Column(db.String(120))
    notes = db.Column(db.Text)
    group_id = db.Column(db.Integer, db.ForeignKey("equipment_group.id"), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    tasks = db.relationship(
        "MaintenanceTask",
        backref="equipment",
        cascade="all, delete-orphan",
        order_by="MaintenanceTask.next_due_date",
        foreign_keys="MaintenanceTask.equipment_id",
    )


class MaintenanceTask(db.Model):
    __tablename__ = "maintenance_task"

    id = db.Column(db.Integer, primary_key=True)

    # A task targets EXACTLY ONE of these: a single piece of equipment, or
    # an entire equipment group (so one task covers everything in the group
    # without duplicating it per item).
    equipment_id = db.Column(db.Integer, db.ForeignKey("equipment.id"), nullable=True)
    group_id = db.Column(db.Integer, db.ForeignKey("equipment_group.id"), nullable=True)

    title = db.Column(db.String(150), nullable=False)
    description = db.Column(db.Text)

    frequency_type = db.Column(db.String(20), nullable=False, default="once")
    frequency_interval = db.Column(db.Integer, default=1)

    next_due_date = db.Column(db.Date, nullable=False)
    reminder_days_before = db.Column(db.Integer, default=3, nullable=False)

    # Overrides EmailSettings.default_to_email if set
    notify_email = db.Column(db.String(255))

    active = db.Column(db.Boolean, default=True, nullable=False)

    last_sent_at = db.Column(db.DateTime)
    last_sent_for_due_date = db.Column(db.Date)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    logs = db.relationship(
        "TaskLog",
        backref="task",
        cascade="all, delete-orphan",
        order_by="TaskLog.id.desc()",
    )

    @property
    def is_group_task(self):
        return self.group_id is not None

    @property
    def target_name(self):
        if self.is_group_task:
            return self.group.name
        return self.equipment.name if self.equipment else "(deleted)"

    @property
    def target_category(self):
        if self.is_group_task:
            return self.group.category or "Group"
        return self.equipment.category if self.equipment else "Other"

    @property
    def target_subtitle(self):
        """Secondary line shown under the target name in the UI."""
        if self.is_group_task:
            return f"Group \u00b7 {self.group.item_count} {pluralize('item', self.group.item_count)}"
        parts = [self.equipment.category] if self.equipment else []
        if self.equipment and self.equipment.location:
            parts.append(self.equipment.location)
        return " \u00b7 ".join(parts)

    @property
    def is_recurring(self):
        return self.frequency_type != "once"

    @property
    def email_sent_for_current_due(self):
        return self.last_sent_for_due_date == self.next_due_date

    @property
    def days_until_due(self):
        return (self.next_due_date - date.today()).days

    @property
    def status(self):
        if not self.active:
            return "completed"
        days = self.days_until_due
        if days < 0:
            return "overdue"
        if days <= (self.reminder_days_before or 0):
            return "due_soon"
        return "scheduled"

    @property
    def frequency_label(self):
        if self.frequency_type == "once":
            return "One-time"
        n = self.frequency_interval or 1
        singular = self.frequency_type[:-1]  # days -> day, weeks -> week, etc.
        return f"Every {fmt_count(n, singular)}"


class TaskLog(db.Model):
    __tablename__ = "task_log"

    id = db.Column(db.Integer, primary_key=True)
    task_id = db.Column(db.Integer, db.ForeignKey("maintenance_task.id"), nullable=False)
    due_date = db.Column(db.Date, nullable=False)
    event = db.Column(db.String(30))  # created, reminder_sent, reminder_failed, completed, test_email
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)
    note = db.Column(db.Text)


class EmailSettings(db.Model):
    __tablename__ = "email_settings"

    id = db.Column(db.Integer, primary_key=True)

    smtp_host = db.Column(db.String(255))
    smtp_port = db.Column(db.Integer, default=587)
    smtp_username = db.Column(db.String(255))
    smtp_password = db.Column(db.String(255))
    use_tls = db.Column(db.Boolean, default=True)
    use_ssl = db.Column(db.Boolean, default=False)

    from_email = db.Column(db.String(255))
    default_to_email = db.Column(db.String(255))

    check_interval_minutes = db.Column(db.Integer, default=60, nullable=False)

    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    @staticmethod
    def get():
        settings = EmailSettings.query.first()
        if not settings:
            settings = EmailSettings()
            db.session.add(settings)
            db.session.commit()
        return settings
