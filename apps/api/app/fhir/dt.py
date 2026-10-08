"""FHIR date and time values.

The rest of the code base works in naive local time. FHIR requires a time zone
on any dateTime that has a time, so values are written with the local offset
and read back as naive local time.
"""

from datetime import date, datetime

# The machine's UTC offset, fixed for the process (datetime.astimezone() per value is slow on Windows).
LOCAL_TZ = datetime.now().astimezone().tzinfo


def fhir_datetime(value: datetime) -> str:
    aware = value if value.tzinfo else value.replace(tzinfo=LOCAL_TZ)
    return aware.isoformat(timespec="seconds")


def fhir_date(value: date | datetime) -> str:
    return (value.date() if isinstance(value, datetime) else value).isoformat()


def parse(value: str | None) -> datetime | None:
    """A FHIR date or dateTime as naive local time (a date becomes midnight)."""
    if not value:
        return None
    if len(value) == 10:
        return datetime.fromisoformat(value)
    if len(value) == 7:  # YYYY-MM
        return datetime.fromisoformat(value + "-01")
    if len(value) == 4:  # YYYY
        return datetime(int(value), 1, 1)
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed.astimezone(LOCAL_TZ).replace(tzinfo=None) if parsed.tzinfo else parsed


def ref_id(reference: str | dict | None) -> str | None:
    """'Patient/pat-0001' (or {'reference': ...}) -> 'pat-0001'."""
    if isinstance(reference, dict):
        reference = reference.get("reference")
    if not reference:
        return None
    return reference.rsplit("/", 1)[-1]


def ref(resource_type: str, resource_id: str, display: str | None = None) -> dict:
    out = {"reference": f"{resource_type}/{resource_id}"}
    if display:
        out["display"] = display
    return out
