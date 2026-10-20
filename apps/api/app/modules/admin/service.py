"""Demo housekeeping: the optional daily reset of the demo data.

With the database the demo data persists, and the seeded timeline (history up to
today, bookings for the next two weeks) slowly goes stale. Public deployments can
set DEMO_DAILY_RESET_HOUR to regenerate it once a day. The daily reset is the
storyline's reset (one regeneration, then her preset), so the hospital storyline
is at step 1 with the default patient every morning: a visitor never has to press
Reset to the start, which takes about a minute and leaks memory (casebook 7.8).
A process tries the daily reset at most once a day, whether or not it succeeds.
"""

import logging
from datetime import datetime

from app.core.config import settings
from app.core.store import Store

log = logging.getLogger("app.admin")

_RESET_LOCK = 0x10C_DA11
_attempted: str | None = None  # the day this process last tried the daily reset


def maybe_daily_reset(store: Store, now: datetime | None = None) -> bool:
    global _attempted
    hour = settings.demo_daily_reset_hour
    now = now or datetime.now()
    today = now.date().isoformat()
    if hour is None or now.hour < hour or _attempted == today:
        return False
    # Another process may be checking at the same moment: only the lock holder goes on, and
    # it reads the marker after taking the lock, so it sees a reset the other just committed.
    if not store.try_lock(_RESET_LOCK) or store.modules.get("daily_reset") == today:
        return False
    from app.modules.storyline import service as storyline

    # One try a day: a failed reset rolls back without the marker, and the intake loop, every 2 s, would regenerate
    # again and again, each time leaking memory (casebook 7.8) until the cap restarts the API. A restart tries again.
    _attempted = today
    try:
        # the demo reset, the preset, and what a reset stops: the running workflows (6.5), the queued AI jobs, the
        # storyline's automation
        storyline.reset(None, reason="daily demo reset")
    except Exception as exc:  # the type only: the message may carry patient data
        log.error("daily demo reset of %s failed (%s); not tried again today in this process", today,
                  type(exc).__name__)
        raise  # the step rolls back and logs the traceback
    store.modules["daily_reset"] = today
    return True
