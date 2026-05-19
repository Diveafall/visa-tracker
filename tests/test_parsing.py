from datetime import date
import pytest
from visa_tracker.parsing import parse_visa_date, CURRENT_SENTINEL


@pytest.mark.parametrize("raw, expected", [
    ("22SEP17", date(2017, 9, 22)),
    ("01JAN18", date(2018, 1, 1)),
    ("15OCT16", date(2016, 10, 15)),
    ("08MAR17", date(2017, 3, 8)),
    ("22MAY17", date(2017, 5, 22)),
    ("01DEC16", date(2016, 12, 1)),
])
def test_parse_normal_dates(raw, expected):
    assert parse_visa_date(raw) == expected


def test_parse_current():
    assert parse_visa_date("C") == CURRENT_SENTINEL
    assert CURRENT_SENTINEL == date(9999, 12, 31)


def test_parse_unavailable():
    assert parse_visa_date("U") is None


def test_parse_whitespace():
    assert parse_visa_date("  22SEP17  ") == date(2017, 9, 22)


def test_parse_invalid_raises():
    with pytest.raises(ValueError):
        parse_visa_date("not a date")
    with pytest.raises(ValueError):
        parse_visa_date("99XYZ99")
