"""Telling the time anywhere, with a fixed clock (no dependence on today's date)."""
from __future__ import annotations

import asyncio
import re
import sys
from datetime import datetime, time, timezone
from pathlib import Path
from zoneinfo import ZoneInfoNotFoundError, available_timezones

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from jarvis_brain import clock  # noqa: E402
from jarvis_brain.tools import TOOLS, ToolBox  # noqa: E402

# Windows has no built-in timezone database; it comes from the tzdata package (a brain
# dependency, installed when an upgrade that adds it is applied).
HAVE_TZ = bool(available_timezones())
needs_tz = pytest.mark.skipif(not HAVE_TZ, reason="timezone database (tzdata) not installed")

# Thursday 4 June 2026, quarter past three in the afternoon in Brisbane (northern summer).
JUNE = datetime(2026, 6, 4, 5, 15, tzinfo=timezone.utc)
# Thursday 15 January 2026, same Brisbane time (southern summer: Sydney on daylight saving).
JANUARY = datetime(2026, 1, 15, 5, 15, tzinfo=timezone.utc)


# --- Speaking times and dates (no timezone database needed) ----------------------------------

@pytest.mark.parametrize("hh, mm, spoken", [
    (15, 15, "quarter past three in the afternoon"),
    (15, 0, "three o'clock in the afternoon"),
    (20, 30, "half past eight in the evening"),
    (9, 7, "seven minutes past nine in the morning"),
    (13, 1, "one minute past one in the afternoon"),
    (6, 45, "quarter to seven in the morning"),
    (10, 50, "ten to eleven in the morning"),
    (22, 10, "ten past ten at night"),
    (0, 0, "midnight"),
    (12, 0, "midday"),
    (23, 45, "quarter to midnight"),
    (11, 40, "twenty to midday"),
    (0, 20, "twenty past midnight"),
])
def test_say_time(hh, mm, spoken) -> None:
    assert clock.say_time(time(hh, mm)) == spoken


def test_say_date() -> None:
    assert clock.say_date(datetime(2026, 6, 4)) == "Thursday the fourth of June"
    assert clock.say_date(datetime(2026, 10, 22)) == "Thursday the twenty-second of October"
    assert clock.say_date(datetime(2026, 3, 31)) == "Tuesday the thirty-first of March"
    assert clock.say_date(datetime(2026, 11, 20)) == "Friday the twentieth of November"


@pytest.mark.parametrize("text, expected", [
    ("15:00", time(15, 0)), ("3pm", time(15, 0)), ("3:30 pm", time(15, 30)),
    ("12am", time(0, 0)), ("12pm", time(12, 0)), ("0930", time(9, 30)), ("9.05", time(9, 5)),
    ("noon", time(12, 0)), ("midnight", time(0, 0)),
])
def test_parse_time(text, expected) -> None:
    assert clock.parse_time(text) == expected


@pytest.mark.parametrize("text", ["25:00", "13pm", "tea time", "9:75"])
def test_parse_time_rejects_nonsense(text) -> None:
    with pytest.raises(ValueError):
        clock.parse_time(text)


# --- Place names to timezones ------------------------------------------------------------------

@pytest.mark.parametrize("place, zone", [
    (None, "Australia/Brisbane"),
    ("", "Australia/Brisbane"),
    ("here", "Australia/Brisbane"),
    ("Tokyo", "Asia/Tokyo"),
    ("UK", "Europe/London"),
    ("the time in Paris", "Europe/Paris"),
    ("US Eastern", "America/New_York"),
    ("Pacific time", "America/Los_Angeles"),
    ("Portland, Maine", "America/New_York"),       # narrows an ambiguous city
    ("Portland, Oregon", "America/Los_Angeles"),
    ("Washington DC", "America/New_York"),
    ("Atlanta, Georgia", "America/New_York"),
    ("Heidelberg, Germany", "Europe/Berlin"),      # unknown town, known country
    ("St. Petersburg, Russia", "Europe/Moscow"),
])
def test_resolve(place, zone) -> None:
    assert clock.resolve_key(place)[1] == zone


def test_spoken_names() -> None:
    assert clock.resolve_key(None)[0] == "Brisbane"
    assert clock.resolve_key("new york")[0] == "New York"
    assert clock.resolve_key("UK")[0] == "UK"


@needs_tz
@pytest.mark.parametrize("place, zone", [
    ("Europe/Lisbon", "Europe/Lisbon"),
    ("asia/kolkata", "Asia/Kolkata"),
    ("Tegucigalpa", "America/Tegucigalpa"),        # only in the timezone database
    ("Louisville", "America/Kentucky/Louisville"),  # not ambiguous with its old alias
])
def test_resolve_from_the_timezone_database(place, zone) -> None:
    assert clock.resolve_key(place)[1] == zone


@pytest.mark.parametrize("place", ["Portland", "USA", "Australia", "Canada", "Birmingham",
                                   "Georgia", "Washington"])
def test_ambiguous_places(place) -> None:
    with pytest.raises(clock.AmbiguousPlace):
        clock.resolve_key(place)


def test_unknown_place() -> None:
    with pytest.raises(clock.UnknownPlace):
        clock.resolve_key("Narnia")
    assert "don't know" in clock.current_time("Narnia", now=JUNE)


def test_place_table_uses_real_zone_names() -> None:
    zones = {zone for options in clock._PLACE_TABLE().values() for _, zone in options}
    assert all(re.fullmatch(r"UTC|[A-Z][A-Za-z_]+(/[A-Z][A-Za-z_]+)+", z) for z in zones)
    if HAVE_TZ:
        assert zones <= available_timezones()


# --- The time now --------------------------------------------------------------------------------

@needs_tz
def test_default_is_brisbane() -> None:
    text = clock.current_time(None, now=JUNE)
    assert text.startswith("In Brisbane it's quarter past three in the afternoon, "
                           "Thursday the fourth of June.")
    assert "Australia/Brisbane" in text and "UTC+10" in text
    assert clock.current_time("here", now=JUNE).startswith("In Brisbane")


@needs_tz
def test_brisbane_has_no_daylight_saving() -> None:
    assert "quarter past three in the afternoon" in clock.current_time(None, now=JANUARY)


@needs_tz
def test_london_follows_daylight_saving() -> None:
    summer = clock.current_time("London", now=JUNE)
    assert "In London it's quarter past six in the morning, Thursday the fourth of June." in summer
    assert "nine hours behind Brisbane" in summer and "BST" in summer
    winter = clock.current_time("london", now=JANUARY)
    assert "quarter past five in the morning" in winter and "ten hours behind" in winter


@needs_tz
def test_new_york_on_the_previous_day() -> None:
    text = clock.current_time("New York", now=JUNE)
    assert "quarter past one in the morning, Thursday the fourth of June" in text
    assert "fourteen hours behind" in text
    text = clock.current_time("New York", now=datetime(2026, 6, 3, 23, 0, tzinfo=timezone.utc))
    assert "seven o'clock in the evening, Wednesday the third of June" in text
    assert "In Brisbane it's Thursday the fourth of June." in text


@needs_tz
def test_sydney_ahead_only_in_summer() -> None:
    assert "one hour ahead of Brisbane" in clock.current_time("Sydney", now=JANUARY)
    assert "same time as Brisbane" in clock.current_time("Sydney", now=JUNE)


@needs_tz
def test_half_hour_offsets() -> None:
    assert "four and a half hours behind" in clock.current_time("India", now=JUNE)


@needs_tz
def test_ambiguity_is_reported_for_a_question() -> None:
    text = clock.current_time("Portland", now=JUNE)
    assert "more than one time zone" in text and "Ask which" in text
    assert "Oregon" in text and "Maine" in text
    assert "America/Los_Angeles" in text and "America/New_York" in text


def test_missing_timezone_database_is_explained(monkeypatch) -> None:
    def no_zones(key):
        raise ZoneInfoNotFoundError(f"No time zone found with key {key}")

    monkeypatch.setattr(clock, "ZoneInfo", no_zones)
    assert "timezone database is missing" in clock.current_time(None, now=JUNE)
    assert "tzdata" in clock.convert_time("15:00", None, "Tokyo", now=JUNE)


# --- Converting a time between places ------------------------------------------------------------

@needs_tz
def test_convert_from_here_to_tokyo() -> None:
    text = clock.convert_time("15:00", None, "Tokyo", "2026-06-04", now=JUNE)
    assert text.startswith("When it's three o'clock in the afternoon on Thursday the fourth of "
                           "June in Brisbane, it's two o'clock in the afternoon on Thursday the "
                           "fourth of June in Tokyo.")


@needs_tz
def test_convert_from_london_to_here() -> None:
    text = clock.convert_time("9:00", "London", None, "2026-06-04", now=JUNE)
    assert "it's six o'clock in the evening on Thursday the fourth of June in Brisbane" in text
    winter = clock.convert_time("9:00", "London", None, "2026-01-15", now=JANUARY)
    assert "seven o'clock in the evening" in winter


@needs_tz
def test_convert_across_the_date_line() -> None:
    text = clock.convert_time("08:00", None, "New York", "2026-06-04", now=JUNE)
    assert ("six o'clock in the evening on Wednesday the third of June in New York, "
            "the day before") in text


@needs_tz
def test_convert_defaults_to_the_next_occurrence() -> None:
    # It's 15:15 in Brisbane, so "9am" means tomorrow morning.
    text = clock.convert_time("9am", None, "London", now=JUNE)
    assert "nine o'clock in the morning on Friday the fifth of June in Brisbane" in text
    assert "midnight on Friday the fifth of June in London" in text


@needs_tz
def test_convert_into_a_daylight_saving_gap() -> None:
    # Sydney's clocks jump from 2am to 3am on Sunday 4 October 2026.
    text = clock.convert_time("02:30", "Sydney", None, "2026-10-04", now=JUNE)
    assert "doesn't exist" in text


@needs_tz
def test_convert_reports_ambiguity() -> None:
    assert "more than one time zone" in clock.convert_time("15:00", None, "Canada", now=JUNE)


# --- As tools ------------------------------------------------------------------------------------

def test_time_tools_are_offered() -> None:
    tools = {t["name"]: t for t in TOOLS}
    assert {"get_time", "convert_time"} <= tools.keys()
    assert tools["convert_time"]["input_schema"]["required"] == ["time"]


@needs_tz
def test_time_tools_run_without_cards() -> None:
    async def no_card(card):
        raise AssertionError("time tools must not show cards")

    box = ToolBox(upgrades=None, show_card=no_card)
    assert asyncio.run(box.run("get_time", {})).startswith("In Brisbane it's ")
    assert "Tokyo" in asyncio.run(box.run("get_time", {"place": "Tokyo"}))
    converted = asyncio.run(box.run("convert_time", {"time": "15:00", "from_place": "",
                                                     "to_place": "Tokyo", "date": ""}))
    assert converted.startswith("When it's three o'clock in the afternoon")
