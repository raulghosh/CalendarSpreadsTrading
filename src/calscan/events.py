"""Loads events.yaml (FOMC/CPI/NFP/OpEx/holidays), maintained by hand. See build plan §1."""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import yaml

from calscan.settings import REPO_ROOT


@dataclass(frozen=True, slots=True)
class Event:
    date: date
    category: str
    label: str


@dataclass(frozen=True, slots=True)
class EventsCalendar:
    events: tuple[Event, ...]

    def in_window(
        self, start: date, end: date, categories: Collection[str] | None = None
    ) -> tuple[Event, ...]:
        return tuple(
            e
            for e in self.events
            if start <= e.date <= end and (categories is None or e.category in categories)
        )


def load_events(path: Path | str = REPO_ROOT / "events.yaml") -> EventsCalendar:
    raw = yaml.safe_load(Path(path).read_text())
    events = [
        Event(
            date=date.fromisoformat(item["date"]),
            category=category,
            label=item.get("label", category),
        )
        for category, items in raw.items()
        for item in items
    ]
    return EventsCalendar(events=tuple(events))
