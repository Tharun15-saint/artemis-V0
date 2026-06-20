"""Regression tests for the Walmart XBRL fiscal-quarter keying fixes.

D1: balance-sheet instants (inventory) must key by FISCAL period, not the
    calendar XBRL frame — a Jan-fiscal-year retailer's Apr-30 balance is calendar
    CYxxxxQ1 but the NEXT fiscal year. The old frame keying shifted inventory +1yr.
D2/D3: revenue must honour concept priority (net sales over total Revenues).
"""

from decimal import Decimal

from data.ingestion.walmart_tier1_ingestion import _extract_fiscal_quarter_maps


def _usd(facts):
    return {"units": {"USD": facts}}


def _build_us_gaap():
    # Two consecutive Q1s for a Jan-fiscal-year retailer (Walmart-like):
    #   FY2022 Q1 ends 2021-04-30   (calendar CY2021Q1)
    #   FY2023 Q1 ends 2022-04-30   (calendar CY2022Q1)  <- the inventory glut
    return {
        "RevenueFromContractWithCustomerExcludingAssessedTax": _usd([
            {"start": "2021-02-01", "end": "2021-04-30", "val": 137159000000,
             "fy": 2022, "fp": "Q1", "frame": "CY2021Q1", "filed": "2021-06-04"},
            {"start": "2022-02-01", "end": "2022-04-30", "val": 140288000000,
             "fy": 2023, "fp": "Q1", "frame": "CY2022Q1", "filed": "2022-06-03"},
        ]),
        # Total revenues (membership incl.) — must NOT be chosen over net sales.
        "Revenues": _usd([
            {"start": "2021-02-01", "end": "2021-04-30", "val": 138310000000,
             "fy": 2022, "fp": "Q1", "frame": "CY2021Q1", "filed": "2021-06-04"},
            {"start": "2022-02-01", "end": "2022-04-30", "val": 141569000000,
             "fy": 2023, "fp": "Q1", "frame": "CY2022Q1", "filed": "2022-06-03"},
        ]),
        "InventoryNet": _usd([
            {"end": "2021-04-30", "val": 46383000000, "fy": 2022, "fp": "Q1",
             "frame": "CY2021Q1I", "filed": "2021-06-04"},
            {"end": "2022-04-30", "val": 61229000000, "fy": 2023, "fp": "Q1",
             "frame": "CY2022Q1I", "filed": "2022-06-03"},
        ]),
    }


def test_inventory_keys_to_fiscal_quarter_not_calendar_frame():
    _, revenue, _, _, _, _, inventory, _ = _extract_fiscal_quarter_maps(_build_us_gaap())
    # The Apr-30-2022 balance ($61.2B glut) belongs to FISCAL FY2023 Q1.
    assert inventory[(2023, 1)] == Decimal("61229000000")
    assert inventory[(2022, 1)] == Decimal("46383000000")
    # The pre-fix bug would have put FY2023's balance under (2022, 1).
    assert inventory[(2022, 1)] != Decimal("61229000000")


def test_revenue_prefers_net_sales_over_total_revenues():
    _, revenue, _, _, _, _, _, _ = _extract_fiscal_quarter_maps(_build_us_gaap())
    assert revenue[(2023, 1)] == Decimal("140288000000")   # net sales
    assert revenue[(2022, 1)] == Decimal("137159000000")   # net sales
    assert revenue[(2023, 1)] != Decimal("141569000000")   # not total revenues


# D4: fiscal labels are derived from the PERIOD-END DATE, never SEC's `fy`/`fp` tags,
# which drift. Anchoring on the end date is collision-free and correct for every era.

def test_comparative_collision_resolved_by_end_date():
    # SEC re-tags the prior-year comparative with the filer's CURRENT fy, so two different
    # quarters collide on one (fy, fp). End-date keying re-homes each to its true quarter.
    us_gaap = {
        "RevenueFromContractWithCustomerExcludingAssessedTax": _usd([
            # real FY2025 Q2
            {"start": "2024-05-01", "end": "2024-07-31", "val": 167770000000,
             "fy": 2025, "fp": "Q2", "frame": None, "filed": "2024-09-01"},
            # prior-year comparative, WRONGLY carrying fy=2025/Q2 — same key, different quarter
            {"start": "2023-05-01", "end": "2023-07-31", "val": 161630000000,
             "fy": 2025, "fp": "Q2", "frame": "CY2023Q2", "filed": "2024-09-01"},
        ]),
    }
    _, revenue, *_ = _extract_fiscal_quarter_maps(us_gaap)
    assert revenue[(2025, 2)] == Decimal("167770000000")   # real quarter, by its end date
    assert revenue[(2024, 2)] == Decimal("161630000000")   # comparative re-homed correctly


def test_misleading_fy_tag_ignored_uses_end_date():
    # WMT's 2012-2014 filings carried a fiscal-year-focus lagged by a full year: the quarter
    # ending 2013-10-31 was tagged fy=2013 but is fiscally FY2014 Q3. End-date keying corrects it.
    us_gaap = {
        "RevenueFromContractWithCustomerExcludingAssessedTax": _usd([
            {"start": "2013-08-01", "end": "2013-10-31", "val": 114880000000,
             "fy": 2013, "fp": "Q3", "frame": None, "filed": "2013-12-05"},
        ]),
    }
    _, revenue, *_ = _extract_fiscal_quarter_maps(us_gaap)
    assert revenue[(2014, 3)] == Decimal("114880000000")   # true fiscal label from end date
    assert (2013, 3) not in revenue                        # not the mislabelled SEC tag


def test_ytd_cumulative_not_treated_as_quarter():
    # A YTD (6-month) fact shares the quarter's end date; the discrete quarter must win and the
    # cumulative must never masquerade as a quarter.
    us_gaap = {
        "RevenueFromContractWithCustomerExcludingAssessedTax": _usd([
            {"start": "2024-05-01", "end": "2024-07-31", "val": 167770000000,   # discrete Q2 (~91d)
             "fy": 2025, "fp": "Q2", "frame": None, "filed": "2024-09-01"},
            {"start": "2024-02-01", "end": "2024-07-31", "val": 327710000000,   # 6-month YTD (~181d)
             "fy": 2025, "fp": "Q2", "frame": None, "filed": "2024-09-01"},
        ]),
    }
    _, revenue, *_ = _extract_fiscal_quarter_maps(us_gaap)
    assert revenue[(2025, 2)] == Decimal("167770000000")   # discrete, not the YTD cumulative
