from __future__ import annotations

from datetime import date

import pytest

from app.ingestion.mmwr import InvalidMMWRWeek, mmwr_week_end, mmwr_week_start


class TestMmwrWeekStart:
    def test_known_reference_dates(self):
        # MMWR calendar: 2023 was a year where Jan 1 fell on a Sunday, so week 1 starts on it.
        assert mmwr_week_start(2023, 1) == date(2023, 1, 1)
        # CDC's published 2023 MMWR calendar puts week 40 at Oct 1-7, 2023.
        assert mmwr_week_start(2023, 40) == date(2023, 10, 1)
        # 2026: Jan 4, 2026 is a Sunday, so week 1 starts on Jan 4 itself.
        assert mmwr_week_start(2026, 1) == date(2026, 1, 4)

    def test_week_end_is_six_days_after_start(self):
        assert mmwr_week_end(2023, 40) == date(2023, 10, 7)

    def test_every_week_starts_on_a_sunday(self):
        for year in (2015, 2020, 2021, 2023, 2026):
            for week in (1, 20, 52):
                assert mmwr_week_start(year, week).weekday() == 6  # Sunday

    def test_consecutive_weeks_are_seven_days_apart(self):
        assert (mmwr_week_start(2024, 10) - mmwr_week_start(2024, 9)).days == 7

    @pytest.mark.parametrize("year", [1899, 2101])
    def test_year_out_of_range_rejected(self, year):
        with pytest.raises(InvalidMMWRWeek):
            mmwr_week_start(year, 1)

    @pytest.mark.parametrize("week", [0, 54, -1])
    def test_week_out_of_range_rejected(self, week):
        with pytest.raises(InvalidMMWRWeek):
            mmwr_week_start(2023, week)

    def test_week_53_valid_in_a_year_that_has_one(self):
        # 2020 had an MMWR week 53 (year ended on a Thursday-ish boundary); just assert it
        # doesn't raise and precedes 2021's week 1.
        start_53 = mmwr_week_start(2020, 53)
        assert start_53 < mmwr_week_start(2021, 1)

    def test_week_53_invalid_in_a_year_without_one(self):
        # 2023 has only 52 MMWR weeks.
        with pytest.raises(InvalidMMWRWeek):
            mmwr_week_start(2023, 53)
