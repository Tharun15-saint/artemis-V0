"""
Narrative correlation for the retail layer.

The financial tables tell you *what* a retailer's numbers did. The earnings-call
corpus (retailer_intelligence_extract) tells you *why* — in management's own words,
under analyst pressure, quarter by quarter. This module joins the two on their
shared temporal key (retailer_id, fiscal_year, fiscal_quarter) so every financial
print carries the call narrative that explains it.

Nothing here fabricates: every signal is a verbatim extract from a real 10-Q / 8-K /
earnings transcript, already stored with its source_url, speaker and confidence.
We only select, rank, and align them to the financial period.
"""

from __future__ import annotations

import re
from typing import Any, Optional

from sqlalchemy.orm import Session

from database.models.retail import (
    RetailerFinancials,
    RetailerIntelligenceExtract,
)

# Which call-signal categories explain which financial metric. The proof that a
# reported number was what it was lives in the management commentary tagged to
# these categories — we pull the verbatim quote from the matching signals.
_METRIC_EVIDENCE_CATEGORIES: dict[str, tuple[str, ...]] = {
    "gross_margin_pct": ("gross_margin_pricing", "apparel_markdown_promotion"),
    "comparable_sales_growth_pct": (
        "apparel_sales_performance",
        "consumer_demand_health",
        "consumer_traffic_basket",
        "category_mix_apparel_share",
    ),
    "inventory_days": ("apparel_inventory_position", "apparel_markdown_promotion"),
    "apparel_revenue_usd": ("apparel_sales_performance", "category_mix_apparel_share"),
}

# Human-readable label + the call-side concept each metric is explained by.
_METRIC_LABELS: dict[str, str] = {
    "gross_margin_pct": "gross margin",
    "comparable_sales_growth_pct": "comparable sales",
    "inventory_days": "inventory position",
    "apparel_revenue_usd": "apparel revenue",
}

_NUMBER_RE = re.compile(
    r"\$\s?\d[\d,]*\.\d+\s*(?:billion|million|bn|B|M)?"   # $14.2B (needs a decimal)
    r"|\$\s?\d[\d,]*\s*(?:billion|million|bn|B|M)"        # $6 billion (bare int needs magnitude)
    r"|\d[\d,]*\.?\d*\s*(?:%|percent|percentage points|pts|bps|basis points)"  # 4.1%, 80 basis points
    r"|\d[\d,]*\.?\d*\s*(?:billion|million)"              # 16.2 billion
    r"|\d[\d,]{3,}\s*stores?",                            # 1,956 stores
    re.I,
)

# Keywords that indicate a signal genuinely discusses a given metric (used to
# pick the most on-point explanation, not just the strongest-worded one).
_METRIC_KEYWORDS: dict[str, tuple[str, ...]] = {
    "gross_margin_pct": ("gross margin", "margin rate", "basis point", "bps", "merchandise margin"),
    "comparable_sales_growth_pct": ("comp", "comparable", "traffic", "transactions", "ticket"),
    "inventory_days": ("inventory", "in-stock", "in stock", "weeks of supply", "receipts"),
    "apparel_revenue_usd": ("apparel", "fashion", "softlines", "style", "denim"),
}


def _numbers_in(text: Optional[str]) -> list[str]:
    """Pull the quantified tokens out of a passage, verbatim, for proof."""
    if not text:
        return []
    seen: list[str] = []
    for m in _NUMBER_RE.findall(text):
        tok = m.strip()
        if tok and tok not in seen:
            seen.append(tok)
    return seen

# Signal strength ranks for ordering the most material call points first.
_STRENGTH_RANK: dict[str, int] = {
    "strong": 3,
    "moderate": 2,
    "weak": 1,
}

# Sentiment scoring for a period-level mood read.
_SENTIMENT_SCORE: dict[str, int] = {
    "positive": 1,
    "mixed": 0,
    "neutral": 0,
    "negative": -1,
}


def _signal_sort_key(row: RetailerIntelligenceExtract) -> tuple:
    """Most material first: strength, then confidence, then has-a-number."""
    strength = _STRENGTH_RANK.get((row.signal_strength or "").lower(), 0)
    confidence = float(row.confidence_score) if row.confidence_score is not None else 0.0
    has_number = 1 if row.contains_number else 0
    return (strength, confidence, has_number)


def _summarize_sentiment(rows: list[RetailerIntelligenceExtract]) -> dict[str, Any]:
    """Aggregate call sentiment for the period into a single directional read."""
    counts = {"positive": 0, "negative": 0, "mixed": 0, "neutral": 0}
    score = 0
    for r in rows:
        s = (r.signal_sentiment or "neutral").lower()
        if s not in counts:
            s = "neutral"
        counts[s] += 1
        score += _SENTIMENT_SCORE.get(s, 0)

    n = len(rows) or 1
    net = score / n
    if net >= 0.25:
        mood = "constructive"
    elif net <= -0.25:
        mood = "cautious"
    else:
        mood = "balanced"

    return {
        "mood": mood,
        "net_score": round(net, 2),
        "signal_count": len(rows),
        "positive": counts["positive"],
        "negative": counts["negative"],
        "mixed": counts["mixed"],
        "neutral": counts["neutral"],
    }


def get_period_signals(
    db: Session,
    retailer_id: int,
    fiscal_year: int,
    fiscal_quarter: int,
    top_n: int = 5,
) -> list[dict[str, Any]]:
    """Top earnings-call signals for one fiscal period, most material first."""
    rows = (
        db.query(RetailerIntelligenceExtract)
        .filter(
            RetailerIntelligenceExtract.retailer_id == retailer_id,
            RetailerIntelligenceExtract.fiscal_year == fiscal_year,
            RetailerIntelligenceExtract.fiscal_quarter == fiscal_quarter,
            RetailerIntelligenceExtract.is_latest.is_(True),
        )
        .all()
    )
    rows.sort(key=_signal_sort_key, reverse=True)

    out: list[dict[str, Any]] = []
    for r in rows[:top_n]:
        out.append(
            {
                "signal_category": r.signal_category,
                "business_segment": r.business_segment,
                "extracted_signal": r.extracted_signal,
                "verbatim_quote": _trim_quote(r.raw_text_passage),
                "numbers_in_quote": _numbers_in(r.extracted_signal) or _numbers_in(r.raw_text_passage),
                "sentiment": r.signal_sentiment,
                "strength": r.signal_strength,
                "is_forward_looking": bool(r.is_forward_looking),
                "number_mentioned": r.number_mentioned,
                "speaker": r.speaker if r.speaker and r.speaker != "Unknown" else None,
                "implication": r.artemis_implication,
                "confidence": float(r.confidence_score) if r.confidence_score is not None else None,
                "source_url": r.source_url,
            }
        )
    return out


def _trim_quote(text: Optional[str], max_len: int = 320) -> Optional[str]:
    """A clean verbatim slice of the call passage — the exact proof."""
    if not text:
        return None
    t = " ".join(text.split())
    if len(t) <= max_len:
        return t
    cut = t[:max_len]
    last = cut.rfind(". ")
    return (cut[: last + 1] if last > 120 else cut).rstrip() + " …"


def build_metric_evidence(
    db: Session,
    retailer_id: int,
    fiscal_year: int,
    fiscal_quarter: int,
    fin: RetailerFinancials,
) -> list[dict[str, Any]]:
    """Tie each reported financial number to the call commentary that explains it.

    For every metric we hold a value for, find the management/analyst signals from
    the categories that explain that metric, and surface the verbatim quote and the
    quantified tokens inside it. This is the exact proof: the number from the
    financial statement, and management's own words for *why* it was that number.
    """
    all_rows = (
        db.query(RetailerIntelligenceExtract)
        .filter(
            RetailerIntelligenceExtract.retailer_id == retailer_id,
            RetailerIntelligenceExtract.fiscal_year == fiscal_year,
            RetailerIntelligenceExtract.fiscal_quarter == fiscal_quarter,
            RetailerIntelligenceExtract.is_latest.is_(True),
        )
        .all()
    )
    by_cat: dict[str, list[RetailerIntelligenceExtract]] = {}
    for r in all_rows:
        by_cat.setdefault(r.signal_category, []).append(r)

    evidence: list[dict[str, Any]] = []
    for metric, categories in _METRIC_EVIDENCE_CATEGORIES.items():
        value = getattr(fin, metric, None)
        if value is None:
            continue

        candidates: list[RetailerIntelligenceExtract] = []
        for cat in categories:
            candidates.extend(by_cat.get(cat, []))
        if not candidates:
            continue

        # Pick the most on-point explanation: first prefer signals that actually
        # discuss this metric (keyword hit), then fall back to materiality.
        keywords = _METRIC_KEYWORDS.get(metric, ())

        def _evidence_key(r: RetailerIntelligenceExtract) -> tuple:
            text = (r.extracted_signal or "").lower()
            on_topic = 1 if any(k in text for k in keywords) else 0
            return (on_topic,) + _signal_sort_key(r)

        candidates.sort(key=_evidence_key, reverse=True)
        best = candidates[0]

        # Format the financial value the way it reads on the statement.
        if metric == "apparel_revenue_usd":
            value_str = f"${float(value) / 1e9:.2f}B"
        elif metric == "inventory_days":
            value_str = f"{float(value):.1f} days"
        else:
            value_str = f"{float(value):.2f}%"

        label = _METRIC_LABELS.get(metric, metric)
        evidence.append(
            {
                "metric": metric,
                "metric_label": label,
                "reported_value": value_str,
                "explained_by_category": best.signal_category,
                "management_says": best.extracted_signal,
                "verbatim_quote": _trim_quote(best.raw_text_passage),
                "numbers_in_quote": _numbers_in(best.extracted_signal)
                or _numbers_in(best.raw_text_passage),
                "speaker": best.speaker if best.speaker and best.speaker != "Unknown" else None,
                "sentiment": best.signal_sentiment,
                "source_url": best.source_url,
                "reasoning": (
                    f"Reported {label} of {value_str} for FY{fiscal_year}Q{fiscal_quarter} is "
                    f"explained on the call: {best.extracted_signal}"
                ),
                "supporting_signal_count": len(candidates),
            }
        )
    return evidence


def correlate_period(
    db: Session,
    retailer_id: int,
    fiscal_year: int,
    fiscal_quarter: int,
    top_n: int = 5,
) -> Optional[dict[str, Any]]:
    """Join one financial period to its earnings-call narrative.

    Returns the financial numbers for the period alongside the top call signals
    and an aggregate sentiment read — or None if there's no financial row.
    """
    fin = (
        db.query(RetailerFinancials)
        .filter(
            RetailerFinancials.retailer_id == retailer_id,
            RetailerFinancials.fiscal_year == fiscal_year,
            RetailerFinancials.fiscal_quarter == fiscal_quarter,
            RetailerFinancials.is_latest.is_(True),
        )
        .first()
    )
    if fin is None:
        return None

    all_rows = (
        db.query(RetailerIntelligenceExtract)
        .filter(
            RetailerIntelligenceExtract.retailer_id == retailer_id,
            RetailerIntelligenceExtract.fiscal_year == fiscal_year,
            RetailerIntelligenceExtract.fiscal_quarter == fiscal_quarter,
            RetailerIntelligenceExtract.is_latest.is_(True),
        )
        .all()
    )

    return {
        "fiscal_year": fiscal_year,
        "fiscal_quarter": fiscal_quarter,
        "period_end_date": str(fin.period_end_date) if fin.period_end_date else None,
        "filing_date": str(fin.filing_date) if fin.filing_date else None,
        "financials": {
            "total_net_sales_usd": (
                float(fin.total_net_sales_usd) if fin.total_net_sales_usd is not None else None
            ),
            "gross_margin_pct": (
                float(fin.gross_margin_pct) if fin.gross_margin_pct is not None else None
            ),
            "comparable_sales_growth_pct": (
                float(fin.comparable_sales_growth_pct)
                if fin.comparable_sales_growth_pct is not None
                else None
            ),
            "inventory_days": (
                float(fin.inventory_days) if fin.inventory_days is not None else None
            ),
            "store_count_total": fin.store_count_total,
            "apparel_revenue_usd": (
                float(fin.apparel_revenue_usd) if fin.apparel_revenue_usd is not None else None
            ),
        },
        "call_sentiment": _summarize_sentiment(all_rows) if all_rows else None,
        "metric_evidence": build_metric_evidence(db, retailer_id, fiscal_year, fiscal_quarter, fin),
        "top_signals": get_period_signals(db, retailer_id, fiscal_year, fiscal_quarter, top_n),
        "total_signals_for_period": len(all_rows),
    }


def correlate_recent_periods(
    db: Session,
    retailer_id: int,
    last_n_quarters: int = 8,
    signals_per_period: int = 3,
) -> list[dict[str, Any]]:
    """Walk the most recent financial periods, attaching call narrative to each."""
    periods = (
        db.query(RetailerFinancials.fiscal_year, RetailerFinancials.fiscal_quarter)
        .filter(
            RetailerFinancials.retailer_id == retailer_id,
            RetailerFinancials.is_latest.is_(True),
        )
        .order_by(
            RetailerFinancials.fiscal_year.desc(),
            RetailerFinancials.fiscal_quarter.desc(),
        )
        .limit(last_n_quarters)
        .all()
    )
    out: list[dict[str, Any]] = []
    for fy, fq in periods:
        corr = correlate_period(db, retailer_id, fy, fq, top_n=signals_per_period)
        if corr is not None:
            out.append(corr)
    return out
