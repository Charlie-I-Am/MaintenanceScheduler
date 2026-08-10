import os
from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

try:
    APP_TZ = ZoneInfo(os.environ.get("TZ", "UTC"))
except ZoneInfoNotFoundError:
    APP_TZ = ZoneInfo("UTC")


def local_today():
    """Today's date, in the app's configured timezone (TZ env var)."""
    return datetime.now(APP_TZ).date()


def utc_now():
    """Current time, UTC and timezone-aware - use for stored timestamps."""
    return datetime.now(timezone.utc)