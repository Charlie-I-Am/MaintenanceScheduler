import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart


class EmailError(Exception):
    pass


def send_email(settings, to_addr, subject, body_text, body_html=None):
    """Send an email using the SMTP config stored in EmailSettings.

    Raises EmailError with a human-readable message on failure.
    """
    if not settings or not settings.smtp_host:
        raise EmailError("SMTP is not configured yet. Go to Settings and fill in your mail server details.")
    if not to_addr:
        raise EmailError("No recipient email address is set for this task or in default settings.")
    if not settings.from_email:
        raise EmailError("No 'From' email address configured in Settings.")

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = settings.from_email
    msg["To"] = to_addr

    msg.attach(MIMEText(body_text, "plain"))
    if body_html:
        msg.attach(MIMEText(body_html, "html"))

    port = settings.smtp_port or (465 if settings.use_ssl else 587)

    try:
        if settings.use_ssl:
            server = smtplib.SMTP_SSL(settings.smtp_host, port, timeout=20)
        else:
            server = smtplib.SMTP(settings.smtp_host, port, timeout=20)

        try:
            server.ehlo()
            if settings.use_tls and not settings.use_ssl:
                server.starttls()
                server.ehlo()
            if settings.smtp_username:
                server.login(settings.smtp_username, settings.smtp_password or "")
            server.sendmail(settings.from_email, [to_addr], msg.as_string())
        finally:
            server.quit()
    except Exception as exc:  # noqa: BLE001 - surface any SMTP error to the user
        raise EmailError(f"Failed to send email: {exc}") from exc


def build_reminder_email(task):
    from .models import pluralize, fmt_count  # local import to avoid circular import

    target_label = task.target_name
    subject = f"Maintenance reminder: {task.title} ({target_label})"

    days = task.days_until_due
    if days < 0:
        due_phrase = f"was due {fmt_count(abs(days), 'day')} ago (OVERDUE)"
    elif days == 0:
        due_phrase = "is due today"
    else:
        due_phrase = f"is due in {fmt_count(days, 'day')}"

    if task.is_group_task:
        target_line = f"Equipment Group: {target_label} ({task.group.item_count} {pluralize('item', task.group.item_count)})"
        items = task.group.equipment
        item_list_text = "".join(f"  - {e.name} ({e.category})\n" for e in items) if items else "  (no equipment currently in this group)\n"
        item_list_html = "".join(f"<li>{e.name} <span style='color:#888'>({e.category})</span></li>" for e in items) if items else "<li style='color:#888'>No equipment currently in this group</li>"
    else:
        eq = task.equipment
        target_line = f"Equipment: {target_label} ({eq.category if eq else 'Other'})"
        item_list_text = ""
        item_list_html = ""

    text = (
        f"Maintenance task reminder\n"
        f"--------------------------\n"
        f"{target_line}\n"
        f"Task: {task.title}\n"
        f"Due date: {task.next_due_date.isoformat()}\n"
        f"Status: {due_phrase}\n\n"
        f"{task.description or ''}\n"
        + (f"\nApplies to:\n{item_list_text}" if item_list_text else "")
    )

    items_html_block = f"<p style='margin-bottom:2px;color:#888'>Applies to:</p><ul style='margin-top:0'>{item_list_html}</ul>" if item_list_html else ""

    html = f"""
    <div style="font-family:Arial,sans-serif;max-width:480px">
      <h2 style="margin-bottom:0">Maintenance Reminder</h2>
      <p style="color:#555;margin-top:4px">{target_line}</p>
      <table style="border-collapse:collapse;width:100%">
        <tr><td style="padding:4px 0;color:#888">Task</td><td style="padding:4px 0"><b>{task.title}</b></td></tr>
        <tr><td style="padding:4px 0;color:#888">Due date</td><td style="padding:4px 0">{task.next_due_date.isoformat()}</td></tr>
        <tr><td style="padding:4px 0;color:#888">Status</td><td style="padding:4px 0">{due_phrase}</td></tr>
      </table>
      <p>{(task.description or '').replace(chr(10), '<br>')}</p>
      {items_html_block}
    </div>
    """
    return subject, text, html
