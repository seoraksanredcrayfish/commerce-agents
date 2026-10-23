# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0

"""Reporting periods as the merchant tools name them (``last_7_days``, ``last_30_days``,
``last_90_days``, or an ISO ``start/end`` range), resolved to inclusive date windows."""

from __future__ import annotations

from datetime import date, timedelta

from pydantic import BaseModel

_NAMED_DAYS = {
    "last_7_days": 7,
    "7d": 7,
    "last_30_days": 30,
    "30d": 30,
    "last_90_days": 90,
    "90d": 90,
    "quarter": 90,
}


class Window(BaseModel):
    start: date
    end: date
    prior_start: date
    prior_end: date

    @property
    def label(self) -> str:
        return f"{self.start.isoformat()}/{self.end.isoformat()}"

    @property
    def prior_label(self) -> str:
        return f"{self.prior_start.isoformat()}/{self.prior_end.isoformat()}"


def _window(start: date, end: date) -> Window:
    length = (end - start).days + 1
    prior_end = start - timedelta(days=1)
    return Window(
        start=start,
        end=end,
        prior_start=prior_end - timedelta(days=length - 1),
        prior_end=prior_end,
    )


def resolve_period(period: str | None, today: date) -> Window:
    """Seven days ending ``today`` unless ``period`` names another window."""
    cleaned = (period or "").strip().lower().replace(" ", "_")
    if "/" in cleaned:
        try:
            start_text, end_text = cleaned.split("/", 1)
            start, end = date.fromisoformat(start_text), date.fromisoformat(end_text)
            if start <= end:
                return _window(start, end)
        except ValueError:
            pass
    days = _NAMED_DAYS.get(cleaned, 7)
    return _window(today - timedelta(days=days - 1), today)
