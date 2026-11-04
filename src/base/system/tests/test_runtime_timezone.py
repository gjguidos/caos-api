"""RUNTIME-CONFIG-0.1: time zone data comes from the image, not a host mount.

Fixed winter and summer instants, so results never depend on the day of the run.
"""

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest

WINTER = datetime(2026, 1, 15, 12, 0, tzinfo=UTC)
SUMMER = datetime(2026, 7, 15, 12, 0, tzinfo=UTC)


@pytest.mark.parametrize("name", ["UTC", "Etc/UTC"])
@pytest.mark.parametrize("instant", [WINTER, SUMMER])
def test_named_utc_zones_have_no_offset(name, instant):
    assert ZoneInfo(name).utcoffset(instant.replace(tzinfo=None)).total_seconds() == 0
    assert instant.astimezone(ZoneInfo(name)).hour == 12


def test_stockholm_resolves_with_its_own_rules():
    stockholm = ZoneInfo("Europe/Stockholm")
    assert WINTER.astimezone(stockholm).isoformat() == "2026-01-15T13:00:00+01:00"
    assert SUMMER.astimezone(stockholm).isoformat() == "2026-07-15T14:00:00+02:00"


def test_aware_utc_timestamps_are_unaffected_by_presentation_zones():
    stockholm = WINTER.astimezone(ZoneInfo("Europe/Stockholm"))
    assert stockholm == WINTER and stockholm.astimezone(UTC) == WINTER
    assert WINTER.isoformat() == "2026-01-15T12:00:00+00:00"


class _Frozen(datetime):
    instant = WINTER

    @classmethod
    def now(cls, tz=None):  # type: ignore[override]
        return cls.instant if tz is None else cls.instant.astimezone(tz)


@pytest.mark.parametrize(
    ("instant", "stockholm", "utc"),
    [
        (WINTER, "2026-01-15T13:00:00+01:00", "2026-01-15T12:00:00+00:00"),
        (SUMMER, "2026-07-15T14:00:00+02:00", "2026-07-15T12:00:00+00:00"),
    ],
)
def test_crawler_report_metadata_resolves_time_zones(monkeypatch, instant, stockholm, utc):
    from src.domains.agentverse.tools.web_crawler_tool.reports import metadata_utils

    monkeypatch.setattr(_Frozen, "instant", instant)
    monkeypatch.setattr(metadata_utils, "datetime", _Frozen)
    assert metadata_utils.build_report_timestamp()["generated_at"] == stockholm
    assert metadata_utils.build_report_timestamp(timezone="UTC")["generated_at"] == utc


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("instant", "stockholm", "utc"),
    [
        (WINTER, "2026-01-15T13:00:00+01:00", "2026-01-15T12:00:00+00:00"),
        (SUMMER, "2026-07-15T14:00:00+02:00", "2026-07-15T12:00:00+00:00"),
    ],
)
async def test_the_date_tool_resolves_time_zones(monkeypatch, instant, stockholm, utc):
    from src.domains.agentverse.tools import date_time_tool

    monkeypatch.setattr(_Frozen, "instant", instant)
    monkeypatch.setattr(date_time_tool, "datetime", _Frozen)
    tool = date_time_tool.DateTool()
    assert (await tool._run(None, timezone="Europe/Stockholm"))["iso_local"] == stockholm
    # The tool's "Today (UTC)" preset: genuinely UTC.
    assert (await tool._run(None, timezone="UTC"))["iso_local"] == utc
