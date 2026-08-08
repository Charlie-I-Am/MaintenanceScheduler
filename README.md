# Maintenance Scheduler

A self-hosted web app for tracking maintenance schedules on servers, appliances,
tools, and other equipment — with automatic email reminders.

**AI Disclosure:** Artificial Intelligence was used during initial prototyping and development.

## Features

- Add **equipment** (servers, appliances, tools, vehicles, HVAC, etc.)
- Organize equipment into **equipment groups** (e.g. "Rack 1 Servers", "Shop
  Power Tools") and assign **one maintenance task to an entire group** instead
  of duplicating the same task per item
- Add **maintenance tasks** targeting either a single piece of equipment or a
  whole group, either one-time or repeating (every N days/weeks/months/years)
- Dashboard shows every task, what it applies to, due date, status (overdue /
  due soon / scheduled / completed), and **whether the reminder email has
  already been sent** for the current due date
- Configurable **email settings** in the web UI: SMTP server, credentials,
  from/to addresses, and how far in advance (per task) to send the reminder
- "Send now" button to manually trigger a task's reminder email, and a
  "Send test email" button in Settings to verify SMTP config
- Mark a task complete — one-time tasks retire, repeating tasks automatically
  roll forward to their next due date
- Per-task history log (task created, reminders sent, failures, completions)
- **All data persists in a `./data` folder** outside the container, so
  rebuilding/upgrading the app image never loses your schedules or settings

## Quick start

```bash
docker compose up -d --build
```

Then open **http://localhost:5000**.

That's it — the SQLite database is created automatically inside `./data/maintenance.db`
on first run.

## Equipment groups

Groups let you avoid creating the same maintenance task over and over for a
set of similar equipment. For example, instead of one "Check RAID health"
task per server, create a **group** called "Rack 1 Servers", put each server
in it, and create **one task** targeting the group — one reminder email
covers the whole rack.

- Go to **Groups** → **Add Group** to create one (name, optional category,
  optional description).
- When adding/editing a piece of **Equipment**, pick a group from the
  dropdown (or leave it as "No group").
- When creating a **Task**, the "Applies to" dropdown lists both individual
  equipment and groups — pick a group to apply the task to everything in it.
- Deleting a group does **not** delete its equipment — the equipment is just
  un-grouped. Tasks assigned directly to that group are removed, since
  there's no longer a group for them to apply to.
- The reminder email for a group task lists every piece of equipment
  currently in the group, so you know exactly what to check.

## Persisting data across upgrades

The `docker-compose.yml` bind-mounts `./data` (on your host) to `/data` (inside
the container), and the app stores its entire database there. To upgrade:

```bash
git pull                     # or however you get the new app code
docker compose up -d --build # rebuilds the image, but ./data is untouched
```

Your equipment, tasks, history, and email settings will all still be there
after the rebuild.

## Configuring email

Go to **Email Settings** in the app. You'll need:

- **SMTP host/port** — e.g. `smtp.gmail.com` port `587` with STARTTLS, or your
  provider's equivalent (Office 365, a self-hosted mail server, SendGrid SMTP
  relay, etc.)
- **Username/password** — for Gmail, use an
  [App Password](https://myaccount.google.com/apppasswords) rather than your
  normal password if 2FA is enabled
- **From address** and a **default recipient** (individual tasks can override
  the recipient)
- **Check interval** — how often (in minutes) the background scheduler checks
  whether any task has entered its reminder window (default: 60 minutes)

Use the **Send Test** button on the Settings page to confirm everything works
before relying on it.

## How reminders work

Each task has a **due date** and a **"remind me N days before"** setting. Once
today's date is within that window, the app sends one reminder email and marks
it as sent for that occurring due date — it won't send duplicates. If you don't
mark the task complete, it stays visible as "Overdue" (and the reminder has
already gone out) until you do.

Marking a task **complete**:
- One-time tasks become inactive (shown as "Completed") but stay in the
  database and history — they're hidden from the dashboard by default, but
  you can still see them by setting the status filter to "Completed".
- Repeating tasks jump to their next due date automatically (e.g. a monthly
  task due Aug 10 becomes due Sep 10), and the "email sent" flag resets so
  you'll get a new reminder for the next cycle.

**Completing** a task is different from **deleting** it: the **Delete**
button on the dashboard permanently removes the task (and its history log)
from the database — there's no undo. Use "Mark complete" for tasks you want
to keep a record of; use "Delete" for tasks you want gone entirely.

## Filtering the dashboard

The dashboard has two filters side by side: **category** and **status**
(Overdue / Due Soon / Scheduled / Completed). By default, completed tasks are
hidden so they don't clutter the view — select "Completed" in the status
filter (or click the Completed count card) to see them. The two filters can
be combined, e.g. category = Server + status = Overdue.

## Updating the app

Replace the app files with the new version, then rebuild:

```bash
docker compose up -d --build
```

`./data` is a bind mount to your host, so it's never touched by a rebuild —
your equipment, groups, tasks, and email settings all carry over.

**About this update specifically:** it adds equipment groups, which means a
new database table and a couple of new columns on existing tables. The app
creates any new tables automatically on startup, and new columns default to
empty/ungrouped for all your existing equipment and tasks — nothing you
already had gets reassigned or lost. You won't need to do anything manually;
just rebuild as usual and your existing equipment/tasks will simply show up
ungrouped until you choose to organize them into groups.

## Changelog

### 0.3.0
- Fixed the category badge on equipment/group cards rendering pinned to the
  top of the card instead of vertically centered next to the name (a flex
  alignment fix).
- Centered the icon/label on the "+ New Task", "+ Add Equipment", and
  "+ Add Group" buttons.
- Task history now logs a "Created" event when a task is first added, with
  what it was created for, alongside reminders sent/failed and completions.
- Dashboard: added a status filter next to the category filter; completed
  tasks are now hidden by default.
- Dashboard: added a working Delete button on each task row.
- Fixed the task edit page's Delete button being silently swallowed by the
  outer edit form (an invalid nested-`<form>` bug) so it now actually
  deletes the task instead of just saving and redirecting.
- Fixed inconsistent (mixed squared/rounded) button shapes on the dashboard
  action row.
- Fixed empty optional fields (email, description, notes, location)
  rendering the literal text "None" in edit forms.

### 0.2.0
- Added **equipment groups**: organize equipment into groups and assign one
  task to an entire group instead of duplicating it per item.
- Automatic singular/plural wording throughout the UI and emails (e.g.
  "1 day" vs "3 days") instead of "day(s)".
- Added an automatic, idempotent database migration so upgrading from a
  pre-groups install doesn't require manual steps.

### 0.1.0
- Initial release: equipment, maintenance tasks (one-time or repeating),
  email reminders with configurable lead time, dashboard with status/email
  tracking, per-task history log, email settings page with test-send, and
  Docker Compose setup with all data persisted in a bind-mounted `./data`
  folder so upgrades never lose data.

## Optional: basic auth

If you expose this app beyond your local network, set `BASIC_AUTH_USER` and
`BASIC_AUTH_PASS` in `docker-compose.yml` to put the whole app behind a simple
HTTP login prompt. Leave both blank to disable (fine for a trusted home LAN).
For anything internet-facing, put it behind a reverse proxy with HTTPS
(Caddy, Traefik, nginx, etc.) rather than relying on basic auth alone.

## Project structure

```
maintenance-scheduler/
├── docker-compose.yml     # defines the service + the persistent ./data volume
├── Dockerfile
├── requirements.txt
├── data/                  # <- persists across rebuilds (SQLite DB lives here)
└── app/
    ├── main.py             # Flask routes / app factory
    ├── models.py           # Equipment, EquipmentGroup, MaintenanceTask, TaskLog, EmailSettings
    ├── scheduler.py        # background job that checks & sends reminders
    ├── email_utils.py      # SMTP sending
    ├── templates/          # Jinja2 + Bootstrap 5 UI
    └── static/style.css
```

## Notes / things to customize

- Set `TZ` in `docker-compose.yml` to your local timezone so "days until due"
  lines up with your calendar.
- The scheduler runs inside the single web process (gunicorn with 1 worker).
  If you ever scale to multiple workers/replicas, move the reminder-check job
  to a separate process to avoid duplicate emails.
- `SECRET_KEY` in `docker-compose.yml` should be changed to a random value.
