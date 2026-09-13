from datetime import date

from calscan.events import load_events


def test_events_yaml_loads_and_filters_window() -> None:
    calendar = load_events()
    assert len(calendar.events) > 20

    window = calendar.in_window(date(2026, 9, 1), date(2026, 9, 30), categories=["fomc"])
    assert [e.date for e in window] == [date(2026, 9, 16)]
