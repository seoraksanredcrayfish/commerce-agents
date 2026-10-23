# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0

"""Reporting periods resolved to inclusive date windows and their prior windows."""

from __future__ import annotations

from datetime import date

from ledger_backend.periods import resolve_period

TODAY = date(2026, 10, 21)


def test_no_period_is_the_last_seven_days_ending_today():
    window = resolve_period(None, TODAY)

    assert (window.start, window.end) == (date(2026, 10, 15), TODAY)
    assert (window.prior_start, window.prior_end) == (date(2026, 10, 8), date(2026, 10, 14))
    assert window.label == "2026-10-15/2026-10-21"


def test_named_windows():
    assert resolve_period("last_30_days", TODAY).start == date(2026, 9, 22)
    assert resolve_period("last_90_days", TODAY).start == date(2026, 7, 24)


def test_an_iso_range_is_taken_as_written_with_a_prior_window_of_the_same_length():
    window = resolve_period("2026-09-01/2026-09-10", TODAY)

    assert (window.start, window.end) == (date(2026, 9, 1), date(2026, 9, 10))
    assert (window.prior_start, window.prior_end) == (date(2026, 8, 22), date(2026, 8, 31))


def test_an_unreadable_period_falls_back_to_seven_days():
    assert resolve_period("sometime", TODAY).start == date(2026, 10, 15)
