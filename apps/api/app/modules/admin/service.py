"""Demo housekeeping: the optional daily reset of the demo data.

With the database the demo data persists, and the seeded timeline (history up to
today, bookings for the next two weeks) slowly goes stale. Public deployments can
set DEMO_DAILY_RESET_HOUR to regenerate it once a day. The daily reset is the
storyline's reset (one regeneration, then her preset), so the hospital storyline
is at step 1 with the default patient every morning: a visitor never has to press
Reset to the start, which takes about a minute and leaks memory (casebook 7.8).
"""

from datetime import datetime

from app.core.config import settings
from app.core.store import Store

_RESET_LOCK = 0x10C_DA11


def maybe_daily_reset(store: Store, now: datetime | None = None) -> bool:
    hour = settings.demo_daily_reset_hour
    now = now or datetime.now()
    if hour is None or now.hour < hour:
        return False
    # Another process may be checking at the same moment: only the lock holder goes on, and
    # it reads the marker after taking the lock, so it sees a reset the other just committed.
    if not store.try_lock(_RESET_LOCK) or store.modules.get("daily_reset") == now.date().isoformat():
        return False
    from app.modules.storyline import service as storyline

    # the demo reset, the preset, and what a reset stops: the running workflows (6.5), the queued AI jobs, the
    # storyline's automation
    storyline.reset(None, reason="daily demo reset")
    store.modules["daily_reset"] = now.date().isoformat()
    return True
