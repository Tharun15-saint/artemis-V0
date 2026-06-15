"""Shared WASDE / cotton marketing year helpers."""

from datetime import date


def current_marketing_year() -> int:
    today = date.today()
    return today.year if today.month >= 8 else today.year - 1
