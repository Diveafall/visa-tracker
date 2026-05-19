from datetime import date

CURRENT_SENTINEL = date(9999, 12, 31)

_MONTH_NUM = {m: i + 1 for i, m in enumerate(
    ["JAN", "FEB", "MAR", "APR", "MAY", "JUN",
     "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"])}


def parse_visa_date(raw: str) -> date | None:
    """Parse the State Dept visa bulletin date format.

    Returns the parsed date for normal entries like '22SEP17',
    CURRENT_SENTINEL for 'C', and None for 'U' (unavailable).
    Raises ValueError on anything else.
    """
    s = raw.strip().upper()
    if s == "C":
        return CURRENT_SENTINEL
    if s == "U":
        return None
    if len(s) != 7:
        raise ValueError(f"Unexpected visa date format: {raw!r}")
    day_str, mon_str, yr_str = s[:2], s[2:5], s[5:7]
    if mon_str not in _MONTH_NUM or not day_str.isdigit() or not yr_str.isdigit():
        raise ValueError(f"Unparseable visa date: {raw!r}")
    yr = 2000 + int(yr_str)
    return date(yr, _MONTH_NUM[mon_str], int(day_str))
