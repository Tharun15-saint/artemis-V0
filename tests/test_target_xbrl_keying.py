"""Regression tests for Target XBRL fiscal-quarter keying.

Target labels are derived from the period-END date (_fiscal_quarter_from_end), never SEC's
`fy`/`fp` tags, which drift. Target's 4-4-5 calendar ends near end-Jan/early-Feb and Target names
a fiscal year for the calendar year it mostly spans (the year ending Feb 3 2018 is 'fiscal 2017').
"""

from decimal import Decimal

from data.ingestion.target_tier1_ingestion import (
    REVENUE_CONCEPTS,
    _extract_fiscal_quarter_maps,
)

_C = REVENUE_CONCEPTS[0]


def _usd(facts):
    return {"units": {"USD": facts}}


def _maps(facts):
    out = _extract_fiscal_quarter_maps({_C: _usd(facts)})
    return out[0], out[1]   # meta, revenue


def test_end_date_fiscal_labels_target_convention():
    # Q1 ends ~Apr/May (fy = end.year); Q4 ends ~Jan/early-Feb (fy = end.year - 1).
    meta, revenue = _maps([
        {"start": "2017-01-29", "end": "2017-04-29", "val": 16220000000,
         "fy": 2018, "fp": "Q1", "frame": "CY2017Q1", "filed": "2017-05-24"},   # FY2017 Q1
        {"start": "2017-10-29", "end": "2018-02-03", "val": 22980000000,
         "fy": 2018, "fp": "Q4", "frame": "CY2017Q4", "filed": "2018-03-07"},   # FY2017 Q4 (53-wk)
    ])
    assert (2017, 1) in revenue and meta[(2017, 1)]["period_end_date"].isoformat() == "2017-04-29"
    assert (2017, 4) in revenue and meta[(2017, 4)]["period_end_date"].isoformat() == "2018-02-03"
    assert (2018, 1) not in revenue   # the SEC fy=2018 tag must NOT be taken literally


def test_comparative_collision_resolved_by_end_date():
    # SEC re-tags the prior-year comparative with the current fy, colliding two quarters on one
    # (fy, fp). End-date keying re-homes each to its true quarter — this is what dropped FY2022 Q3.
    meta, revenue = _maps([
        {"start": "2022-07-31", "end": "2022-10-29", "val": 26520000000,
         "fy": 2023, "fp": "Q3", "frame": None, "filed": "2022-11-23"},          # real FY2022 Q3
        {"start": "2021-08-01", "end": "2021-10-30", "val": 25650000000,
         "fy": 2023, "fp": "Q3", "frame": "CY2021Q3", "filed": "2022-11-23"},    # comparative
    ])
    assert revenue[(2022, 3)] == Decimal("26520000000")   # real quarter by its end date
    assert revenue[(2021, 3)] == Decimal("25650000000")   # comparative re-homed correctly


def test_ytd_cumulative_not_treated_as_quarter():
    meta, revenue = _maps([
        {"start": "2022-07-31", "end": "2022-10-29", "val": 26520000000,        # discrete Q3 (~90d)
         "fy": 2023, "fp": "Q3", "frame": None, "filed": "2022-11-23"},
        {"start": "2022-01-30", "end": "2022-10-29", "val": 78000000000,        # 9-month YTD (~272d)
         "fy": 2023, "fp": "Q3", "frame": None, "filed": "2022-11-23"},
    ])
    assert revenue[(2022, 3)] == Decimal("26520000000")   # discrete, not the YTD cumulative
