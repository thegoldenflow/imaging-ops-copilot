"""Demo housekeeping: the optional daily reset of the demo data.

With the database the demo data persists, and the seeded timeline (history up to
today, bookings for the next two weeks) slowly goes stale. Public deployments can
set DEMO_DAILY_RESET_HOUR to regenerate it once a day.
"""

from datetime import datetime

from app.core.config import settings
from app.core.store import Store, reset_store

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
    reset_store()
    store.modules["daily_reset"] = now.date().isoformat()
    store.audit.record(user_id="system", user_name="Scheduler", role="system", action="reset",
                       resource_type="demo_data", resource_id=None, reason="daily demo reset")
    from app.workflows import bridge

    bridge.on_reset()  # the running workflows refer to data that is gone (6.5)
    return True
