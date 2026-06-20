"""
v5.0 spoken-signal extraction — comprehensive, two-axis-tagged, turn-anchored.

Runs OVER the Layer-2 structured transcript (transcript_turn), not raw text. For each non-operator
turn it asks Opus for EVERY material signal (capture-all, never delete), tags each on two axes
(TOPIC + RELEVANCE TIER), and anchors each to an EXACT verbatim quote whose char offsets locate it
inside the immutable raw L1 — so faithfulness is provable BY CONSTRUCTION. Q&A answers are given
their analyst question as context (and linked via replies_to_turn_index).

Capture scope (agreed with the operator):
  CORE (utmost precision, every minute detail): apparel category performance; consumer demand/health;
    inventory turnover + sell-through + days; pricing/margin + markdown depth; sourcing/vendor;
    tariff/trade; store footprint; strategy/tech; channel mix incl. e-commerce penetration (and which
    categories have online power); financial-health metrics.
  SECONDARY (captured, tagged lower priority): capital_allocation, m_and_a, resale_secondhand.
  EXCLUDE: executive/governance changes, pure financial-engineering guidance (EPS/tax/share count)
    unless tied to a demand/category/margin story, legal boilerplate, content-free pleasantries.
Nothing is lost: the COMPLETE transcript already lives in L1 (raw) + L2 (every turn); extraction is an
additive, filterable layer on top.

    python -m data.ingestion.transcript_extractor_v5 --retailer-id 2 --fy 2023 --fq 1   # pilot
    python -m data.ingestion.transcript_extractor_v5                                     # full corpus
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import time
from decimal import Decimal

from anthropic import Anthropic
from sqlalchemy import text

from data.ingestion._env import load_project_env
from database.base import SessionLocal, mark_latest
from database.models import RetailerIntelligenceExtract, TranscriptTurn

load_project_env()
logger = logging.getLogger("transcript_extractor_v5")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

EXTRACTION_MODEL = "claude-opus-4-8"
EXTRACTION_PROMPT_VER = "v5.0"
_RATE_LIMIT_S = 0.4
_MAX_RETRIES = 4

MODELS = {"opus": "claude-opus-4-8", "sonnet": "claude-sonnet-4-6"}
# (input $/M, output $/M). cache read = 0.1x input, cache write = 1.25x input.
PRICES = {"claude-opus-4-8": (5.0, 25.0), "claude-sonnet-4-6": (3.0, 15.0)}


def _cost(model: str, u: dict) -> float:
    pin, pout = PRICES[model]
    return ((u["in"] + 0.1 * u["cache_read"] + 1.25 * u["cache_write"]) * pin
            + u["out"] * pout) / 1e6

TOPICS = {
    "apparel_performance", "consumer_demand", "inventory_sellthrough", "pricing_margin",
    "sourcing_vendor", "tariff_trade", "store_footprint", "strategy_tech", "channel_mix",
    "financial_performance", "capital_allocation", "m_and_a", "resale_secondhand",
}
TIERS = {"core_apparel", "demand_adjacent", "macro_context", "secondary"}

SYSTEM_PROMPT = (
    "You are the retail-intelligence layer of Artemis, the operating system an apparel operator "
    "(e.g. Classic Fashion, which manufactures hundreds of millions of garments a year for retailers "
    "like Walmart and Target) uses to run its supply chain from cotton to consumer. What a retailer "
    "says on an earnings call flows downstream into program volumes, factory capacity, yarn timing and "
    "hedging. The financials give the numbers; your job is the COLOR — the texture only the transcript "
    "carries.\n\n"
    "You are given ONE turn from a retailer earnings call. Extract EVERY material signal it contains — "
    "be COMPREHENSIVE, not selective; a turn may yield zero, one, or many signals. Do NOT summarize the "
    "whole turn into one signal; emit one signal per DISTINCT material claim. Capturing everything in "
    "scope, precisely, matters more than brevity. Return a JSON ARRAY of signal objects; return [] if "
    "the turn has no in-scope content.\n\n"
    "CAPTURE SCOPE:\n"
    "CORE — capture at the utmost precision, every minute detail, including exact numbers/percentages:\n"
    "  • apparel_performance — apparel/fashion/softlines/footwear/active: sales, comps, sell-through, "
    "share, momentum, and the performance of SPECIFIC clothing categories.\n"
    "  • consumer_demand — consumer health, trade-down/up, basket shifts, discretionary spend, "
    "value-seeking, trip frequency.\n"
    "  • inventory_sellthrough — inventory TURNOVER, SELL-THROUGH rates, days/weeks on hand, "
    "in-stock/lean vs excess, clearance — capture the figures precisely.\n"
    "  • pricing_margin — gross MARGIN levels/changes, MARKDOWN depth (how much they mark down, by "
    "category/product where stated), promo intensity, pricing strategy, cost pressure passed to vendors. "
    "Capture exact margin and markdown numbers.\n"
    "  • sourcing_vendor — vendor base, sourcing geography, direct sourcing, private vs national brand, "
    "lead times, supplier compliance.\n"
    "  • tariff_trade — tariffs, trade policy, country-of-origin, Section 301, de minimis, nearshoring.\n"
    "  • store_footprint — openings/closures/remodels/formats/square footage (esp. apparel floor space).\n"
    "  • strategy_tech — strategy shifts, AI/personalization, trend forecasting, omnichannel, and what "
    "the retailer will demand of suppliers.\n"
    "  • channel_mix — digital vs physical, owned vs marketplace, and especially E-COMMERCE PENETRATION "
    "— note which PRODUCT CATEGORIES are strong online (this tells us which categories have online power).\n"
    "  • financial_performance — total/segment sales, comps, revenue, margins and other health metrics, "
    "plus forward guidance ON THESE. Capture the metrics that reveal retailer health and likely behavior.\n"
    "SECONDARY — capture, but it is lower-priority backdrop:\n"
    "  • capital_allocation (capex/buybacks/dividends), m_and_a (mergers/acquisitions/divestitures), "
    "resale_secondhand (resale, recommerce, second-hand/thrift apparel programs).\n"
    "EXCLUDE — do not emit a signal: executive/leadership/board/governance/compensation changes; pure "
    "financial-engineering guidance (EPS, tax rate, share count, interest expense) UNLESS tied to a "
    "demand/category/margin story; legal or safe-harbor boilerplate; content-free pleasantries or "
    "motivational language with no operational fact.\n"
    "When unsure whether something is in scope, INCLUDE it and tag relevance_tier='macro_context' — "
    "bias to capture, never silently drop, but never invent a signal that isn't in the text.\n\n"
    "TWO-AXIS TAGS per signal:\n"
    "  topic_category — exactly one of: apparel_performance, consumer_demand, inventory_sellthrough, "
    "pricing_margin, sourcing_vendor, tariff_trade, store_footprint, strategy_tech, channel_mix, "
    "financial_performance, capital_allocation, m_and_a, resale_secondhand.\n"
    "  relevance_tier — how central to the apparel operator: 'core_apparel' (directly about "
    "apparel/fashion — performance, apparel inventory/sourcing/pricing/floor space); 'demand_adjacent' "
    "(consumer/inventory/margin/sourcing/strategy that strongly bears on apparel demand or supply even "
    "if not apparel-named); 'macro_context' (broad consumer/economic/total-company backdrop); "
    "'secondary' (capital_allocation/m_and_a/resale_secondhand).\n\n"
    "Each signal object MUST have:\n"
    "  verbatim_quote: the EXACT substring of the passage carrying the signal — copied "
    "character-for-character (no paraphrase, no ellipsis, no edits); keep it to the specific sentence(s). "
    "It must be findable verbatim in the passage.\n"
    "  neutral_summary: one factual sentence of what was said (no spin).\n"
    "  topic_category, relevance_tier (as above).\n"
    "  signal_sentiment: positive | negative | neutral | mixed (for the retailer's apparel/demand outlook).\n"
    "  signal_strength: strong | moderate | weak.\n"
    "  is_forward_looking: true/false. contains_number: true/false. "
    "number_mentioned: the key figure as a short string (e.g. 'inventory down 8%', 'gross margin -120bps') "
    "or null. time_horizon: current_quarter | next_quarter | fiscal_year | multi_year | null. "
    "business_segment: walmart_us | sams_club | target_us | international | enterprise | null.\n"
    "  operator_implication: one sentence on what it means for the apparel operator (Classic Fashion) — "
    "only if there is a real implication, else null.\n"
    "  confidence: 0.0-1.0 that this is a faithful, in-scope signal.\n\n"
    "Output ONLY the JSON array."
)


def _call_model(client: Anthropic, model: str, user_prompt: str, usage: dict) -> str | None:
    """Call the model; accumulate token usage into `usage`. System prompt is cache-flagged
    (helps where the prefix clears the model's cache floor — Sonnet 2048, Opus 4096)."""
    delay = _RATE_LIMIT_S
    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            # STREAM the response: high max_tokens / long outputs can stall a non-streaming request
            # indefinitely (observed: a runaway turn hung the whole run). Streaming reads events
            # incrementally so a slow/hanging request fails fast and retries instead of blocking.
            with client.messages.stream(
                model=model, max_tokens=8000,
                system=[{"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}],
                messages=[{"role": "user", "content": user_prompt}]) as stream:
                resp = stream.get_final_message()
            time.sleep(_RATE_LIMIT_S)
            u = resp.usage
            usage["in"] += getattr(u, "input_tokens", 0) or 0
            usage["out"] += getattr(u, "output_tokens", 0) or 0
            usage["cache_read"] += getattr(u, "cache_read_input_tokens", 0) or 0
            usage["cache_write"] += getattr(u, "cache_creation_input_tokens", 0) or 0
            usage["calls"] += 1
            return resp.content[0].text if resp.content else None
        except Exception as exc:                              # noqa: BLE001
            if attempt < _MAX_RETRIES:
                logger.warning("%s call failed (%d/%d): %s — retry in %.1fs", model, attempt, _MAX_RETRIES, exc, delay)
                time.sleep(delay)
                delay *= 2
            else:
                logger.error("%s call failed after %d attempts: %s", model, _MAX_RETRIES, exc)
    return None


def _salvage_objects(s: str) -> list:
    """Extract every top-level {...} object from a (possibly truncated/malformed) JSON array,
    decoding each independently so one bad or incomplete object can't sink the rest. Brace
    counting is string/escape aware so braces inside quoted text don't confuse nesting."""
    out, i, n = [], 0, len(s)
    while i < n:
        if s[i] != "{":
            i += 1
            continue
        depth = 0
        in_str = esc = False
        j = i
        closed = False
        while j < n:
            c = s[j]
            if in_str:
                if esc:
                    esc = False
                elif c == "\\":
                    esc = True
                elif c == '"':
                    in_str = False
            elif c == '"':
                in_str = True
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    try:
                        out.append(json.loads(s[i:j + 1]))
                    except json.JSONDecodeError:
                        pass
                    i = j + 1
                    closed = True
                    break
            j += 1
        if not closed:                                       # unterminated final object → stop
            break
    return out


def _parse_json_array(raw: str):
    """Tolerant: never raises. Returns a list of signal dicts (possibly partial) or None.
    A malformed/truncated model response degrades to the salvageable objects, never a crash."""
    s = re.sub(r"^```(?:json)?\s*", "", raw.strip())
    s = re.sub(r"\s*```$", "", s).strip()
    try:
        v = json.loads(s)
        return v if isinstance(v, list) else [v] if isinstance(v, dict) else None
    except json.JSONDecodeError:
        pass
    m = re.search(r"\[.*\]", s, re.DOTALL)
    if m:
        try:
            return json.loads(m.group())
        except json.JSONDecodeError:
            pass
    objs = _salvage_objects(s)                                # last resort: per-object salvage
    if objs:
        logger.warning("salvaged %d object(s) from malformed/truncated JSON response", len(objs))
    return objs or None


def _anchor(quote: str, turn) -> tuple[int, int] | None:
    """Locate the verbatim quote inside the turn → absolute char offsets into raw content.
    Faithfulness BY CONSTRUCTION: only exact (or whitespace-equivalent) matches anchor."""
    txt = turn.verbatim_text
    i = txt.find(quote)
    if i != -1:
        return turn.char_start + i, turn.char_start + i + len(quote)
    # whitespace-tolerant: collapse runs of whitespace, map the match back to original offsets
    norm = re.sub(r"\s+", " ", quote).strip()
    flat = re.sub(r"\s+", " ", txt)
    j = flat.find(norm)
    if j == -1:
        return None
    # walk original text counting collapsed positions to recover real start/end
    def real_offset(collapsed_idx: int) -> int:
        seen, k, in_ws = 0, 0, False
        while k < len(txt):
            c = txt[k]
            if c.isspace():
                if not in_ws:
                    if seen == collapsed_idx:
                        return k
                    seen += 1
                    in_ws = True
            else:
                if seen == collapsed_idx:
                    return k
                seen += 1
                in_ws = False
            k += 1
        return len(txt)
    start = real_offset(j)
    end = real_offset(j + len(norm))
    return turn.char_start + start, turn.char_start + end


def _trunc(v, n):
    if v is None:
        return None
    s = str(v).strip()
    return s[:n] if s else None


def _dec(v):
    try:
        return Decimal(str(v)) if v is not None else None
    except Exception:
        return None


def process_quarter(db, client, rid, fy, fq, model=EXTRACTION_MODEL, usage=None, dry_run=False) -> dict:
    if usage is None:
        usage = {"in": 0, "out": 0, "cache_read": 0, "cache_write": 0, "calls": 0}
    turns = (db.query(TranscriptTurn)
             .filter(TranscriptTurn.retailer_id == rid, TranscriptTurn.fiscal_year == fy,
                     TranscriptTurn.fiscal_quarter == fq, TranscriptTurn.is_latest.is_(True))
             .order_by(TranscriptTurn.turn_index).all())
    if not turns:
        logger.warning("no L2 turns for r%s FY%sQ%s", rid, fy, fq)
        return {"signals": 0}
    by_index = {t.turn_index: t for t in turns}
    period_end = turns[0].period_end_date

    pending, unanchored = [], 0
    for t in turns:
        if t.speaker_role == "operator" or not t.verbatim_text.strip():
            continue
        ctx = ""
        if t.replies_to_turn_index is not None and t.replies_to_turn_index in by_index:
            q = by_index[t.replies_to_turn_index]
            ctx = (f"ANALYST QUESTION (context — do NOT quote from this):\n"
                   f"{q.speaker_name}: {q.verbatim_text[:1200]}\n\n")
        user = (f"{ctx}PASSAGE (turn {t.turn_index}, section={t.section}, "
                f"speaker={t.speaker_name or '?'}, role={t.speaker_role}):\n{t.verbatim_text}")
        raw = _call_model(client, model, user, usage)
        if not raw:
            continue
        signals = _parse_json_array(raw)
        if not isinstance(signals, list):
            continue
        for sig in signals:
            if not isinstance(sig, dict):
                continue
            quote = sig.get("verbatim_quote")
            topic = sig.get("topic_category")
            if not quote or topic not in TOPICS:
                continue
            anchor = _anchor(quote, t)
            if anchor is None:
                unanchored += 1
                continue
            sig["_turn"] = t
            sig["_anchor"] = anchor
            # store the RAW SLICE (not the model's quote) so raw_text_passage is provably
            # == raw_content[quote_char_start:quote_char_end] — faithfulness by construction.
            sig["_passage"] = t.verbatim_text[anchor[0] - t.char_start: anchor[1] - t.char_start]
            pending.append(sig)

    if dry_run:
        # don't touch the DB — return serializable signals for side-by-side review
        out = [{"topic": s["topic_category"], "tier": s.get("relevance_tier"),
                "sentiment": s.get("signal_sentiment"), "number": s.get("number_mentioned"),
                "speaker": s["_turn"].speaker_name, "section": s["_turn"].section,
                "quote": s["_passage"], "summary": s.get("neutral_summary"),
                "implication": s.get("operator_implication"), "confidence": s.get("confidence")}
               for s in pending]
        logger.info("[dry-run %s] r%s FY%sQ%s -> %d signals (unanchored=%d)", model, rid, fy, fq, len(out), unanchored)
        return {"signals": len(out), "unanchored": unanchored, "dry_signals": out}

    if not pending:
        logger.warning("0 signals r%s FY%sQ%s (unanchored=%d) — NOT superseding", rid, fy, fq, unanchored)
        return {"signals": 0, "unanchored": unanchored}

    mark_latest(db, RetailerIntelligenceExtract, {"retailer_id": rid, "fiscal_year": fy, "fiscal_quarter": fq})
    db.flush()
    cal_y = period_end.year if period_end else None
    cal_q = ((period_end.month - 1) // 3 + 1) if period_end else None
    for sig in pending:
        t = sig["_turn"]
        qs, qe = sig["_anchor"]
        tier = sig.get("relevance_tier") if sig.get("relevance_tier") in TIERS else "macro_context"
        db.add(RetailerIntelligenceExtract(
            retailer_id=rid, fiscal_year=fy, fiscal_quarter=fq, period_end_date=period_end,
            calendar_year=cal_y, calendar_quarter=cal_q,
            document_type="earnings_call_transcript", document_section=t.section,
            source_url=t.source_url, source="fmp",
            signal_category=topic_to_legacy(sig["topic_category"]), canonical_category=sig["topic_category"],
            relevance_tier=tier, business_segment=_trunc(sig.get("business_segment"), 40),
            raw_text_passage=sig["_passage"],
            extracted_signal=_trunc(sig.get("neutral_summary"), 500),
            signal_sentiment=_trunc(sig.get("signal_sentiment"), 20),
            signal_strength=_trunc(sig.get("signal_strength"), 20),
            artemis_implication=_trunc(sig.get("operator_implication"), 500),
            artemis_implication_full=sig.get("operator_implication"),
            confidence_score=_dec(sig.get("confidence")),
            speaker=_trunc(t.speaker_role, 20), primary_speaker=_trunc(t.speaker_name, 20),
            is_forward_looking=bool(sig.get("is_forward_looking")),
            contains_number=bool(sig.get("contains_number")),
            number_mentioned=_trunc(sig.get("number_mentioned"), 255),
            time_horizon=_trunc(sig.get("time_horizon"), 30),
            source_turn_index=t.turn_index, quote_char_start=qs, quote_char_end=qe,
            replies_to_turn_index=t.replies_to_turn_index,
            extraction_model=model, extraction_prompt_ver=EXTRACTION_PROMPT_VER,
            human_verified=False, is_latest=True))
    db.commit()
    logger.info("r%s FY%sQ%s -> %d signals (unanchored dropped=%d)", rid, fy, fq, len(pending), unanchored)
    return {"signals": len(pending), "unanchored": unanchored}


# topic_category → legacy signal_category bucket (keeps older consumers working)
_LEGACY = {
    "apparel_performance": "apparel_performance", "consumer_demand": "consumer_behavior",
    "inventory_sellthrough": "inventory_health", "pricing_margin": "margin_pricing",
    "sourcing_vendor": "sourcing_vendor", "tariff_trade": "tariff_trade",
    "store_footprint": "store_expansion", "strategy_tech": "retail_strategy",
    "channel_mix": "channel_mix", "financial_performance": "financial_performance",
    "capital_allocation": "capital_allocation", "m_and_a": "m_and_a",
    "resale_secondhand": "resale_secondhand",
}


def topic_to_legacy(topic: str) -> str:
    return _LEGACY.get(topic, topic)


def _quarters(db, rid, fy, fq, limit):
    sql = ("SELECT retailer_id, fiscal_year, fiscal_quarter FROM transcript_turn WHERE is_latest")
    if rid:
        sql += f" AND retailer_id={int(rid)}"
    if fy:
        sql += f" AND fiscal_year={int(fy)}"
    if fq:
        sql += f" AND fiscal_quarter={int(fq)}"
    sql += " GROUP BY 1,2,3 ORDER BY 1,2,3"
    rows = [(r[0], r[1], r[2]) for r in db.execute(text(sql)).fetchall()]
    return rows[:limit] if limit else rows


def main() -> int:
    p = argparse.ArgumentParser(description="v5.0 turn-anchored transcript extraction")
    p.add_argument("--retailer-id", type=int)
    p.add_argument("--fy", type=int)
    p.add_argument("--fq", type=int)
    p.add_argument("--limit", type=int)
    p.add_argument("--model", choices=list(MODELS), default="opus")
    p.add_argument("--dry-run", action="store_true", help="extract + measure cost, do NOT write DB")
    p.add_argument("--dump", help="write dry-run signals to this JSON path")
    args = p.parse_args()
    if not os.getenv("ANTHROPIC_API_KEY"):
        print("ANTHROPIC_API_KEY not set")
        return 1
    model = MODELS[args.model]
    db = SessionLocal()
    client = Anthropic(timeout=300, max_retries=0)            # our loop retries; cap per-request hang
    usage = {"in": 0, "out": 0, "cache_read": 0, "cache_write": 0, "calls": 0}
    try:
        qs = _quarters(db, args.retailer_id, args.fy, args.fq, args.limit)
        total, dump = 0, []
        for rid, fy, fq in qs:
            r = process_quarter(db, client, rid, fy, fq, model=model, usage=usage, dry_run=args.dry_run)
            total += r.get("signals", 0)
            dump += r.get("dry_signals", [])
        cost = _cost(model, usage)
        mode = "DRY-RUN" if args.dry_run else "WROTE"
        print(f"\n✓ v5.0 {mode} [{model}]: {total} signals across {len(qs)} quarter(s)")
        print(f"  tokens: in={usage['in']:,} out={usage['out']:,} "
              f"cache_read={usage['cache_read']:,} cache_write={usage['cache_write']:,} "
              f"over {usage['calls']} calls")
        print(f"  COST: ${cost:.4f}  (${cost / total:.5f}/signal)" if total else f"  COST: ${cost:.4f}")
        if args.dump and dump:
            with open(args.dump, "w") as f:
                json.dump(dump, f, indent=1, default=str)
            print(f"  dry-run signals → {args.dump}")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
