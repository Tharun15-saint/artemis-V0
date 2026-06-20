"""
Spoken-signal Layer 2 builder: parse the immutable raw L1 transcript (raw_artifact,
artifact_kind='fmp_earnings_transcript') into the COMPLETE, addressable structured transcript —
one `transcript_turn` row per speaker turn, with EXACT char offsets into the raw `content`.

Guarantees (verified by the audit gate):
  - Contiguous coverage: turns tile [0, len(content)) with no gap/overlap, so concatenating
    `verbatim_text` in turn order reconstructs the raw byte-for-byte. This is what makes signal
    faithfulness provable BY CONSTRUCTION (a quote is content[char_start:char_end]).
  - Roles/sections/Q↔A derived deterministically; nothing fabricated (titles/firms left NULL
    when not present in the source).

Deterministic + offline (reads only the CAS) + re-runnable (supersede-then-insert per quarter).

    python -m data.ingestion.transcript_structurer            # all captured quarters
    python -m data.ingestion.transcript_structurer --retailer-id 2 --fy 2023 --fq 1
"""

from __future__ import annotations

import argparse
import json
import logging
import re

from sqlalchemy import text

from data.ingestion._env import load_project_env
from data.raw.raw_store import RawStore
from database.base import SessionLocal
from database.models import TranscriptTurn

load_project_env()
logger = logging.getLogger("transcript_structurer")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

# A speaker header at a line start: "Operator:" or a 2-5 word capitalised name then ": ".
# Requiring ≥2 capitalised words (or the literal "Operator") avoids matching stray lines like
# "Note:" / "Q1:" while capturing real participants ("Doug McMillon:", "Carol Schumacher:").
_TURN_HEADER_RE = re.compile(
    r"(?m)^[ \t]*(Operator|[A-Z][A-Za-z.'’\-]+(?:\s+[A-Z][A-Za-z.'’\-&]+){1,4})\s*:\s")
# The REAL prepared→Q&A boundary is the operator actively handing to the first analyst
# ("our first question comes from the line of …"), NOT an intro mention of a "Q&A session".
# Matching the operator's handoff turn avoids mislabeling the CEO/CFO prepared remarks as Q&A.
_QA_HANDOFF_RE = re.compile(
    r"(?:first|next)\s+questions?"
    r"|questions?\s+(?:comes?|will\s+come)\s+from"
    r"|from\s+the\s+line\s+of"
    r"|open\s+(?:up\s+)?(?:the\s+)?(?:floor|lines?|call)\s+(?:up\s+)?for\s+questions?"
    r"|we['’]?ll\s+(?:now\s+)?(?:take|move\s+to|go\s+to)\s+(?:our\s+)?(?:first\s+)?questions?",
    re.I)
# Fallback boundary if no clean operator handoff is found (must land after a real monologue).
_QA_SPLIT_RE = re.compile(
    r"(?:(?:first|next)\s+questions?\s+(?:comes?\s+from|is\s+from)"
    r"|open\s+(?:the\s+)?(?:floor|line)\s+for\s+questions?"
    r"|Question[\-\s]and[\-\s]Answer\s+Session"
    r"|Q\s*&\s*A\s+Session)", re.I)


def structure_content(content: str) -> list[dict]:
    """Raw transcript content → ordered list of turn dicts that tile [0, len(content))."""
    matches = list(_TURN_HEADER_RE.finditer(content))
    spans: list[tuple[int, int, str | None]] = []
    if not matches:
        spans.append((0, len(content), None))
    else:
        if matches[0].start() > 0:
            spans.append((0, matches[0].start(), None))      # leading preamble (keep contiguity)
        for i, m in enumerate(matches):
            end = matches[i + 1].start() if i + 1 < len(matches) else len(content)
            spans.append((m.start(), end, m.group(1).strip()))

    turns = []
    for idx, (start, end, name) in enumerate(spans):
        verbatim = content[start:end]
        is_operator = bool(name) and name.lower().startswith("operator")
        utt = verbatim
        if name:
            colon = verbatim.find(":")
            utt = verbatim[colon + 1:] if colon != -1 else verbatim
        turns.append({
            "turn_index": idx, "char_start": start, "char_end": end,
            "verbatim_text": verbatim, "speaker_name": name,
            "is_operator": is_operator, "word_count": len(utt.split()), "utt": utt,
        })

    # Q&A starts at the first operator turn that actually hands off to a questioner.
    qa_start = next((t["turn_index"] for t in turns
                     if t["is_operator"] and _QA_HANDOFF_RE.search(t["utt"])), None)
    if qa_start is None:                                      # fallback after a real monologue
        first_mono_end = next((t["char_end"] for t in turns
                               if t["speaker_name"] and not t["is_operator"] and t["word_count"] > 200), None)
        m = _QA_SPLIT_RE.search(content, first_mono_end or 0)
        if m:
            qa_start = next((t["turn_index"] for t in turns if t["char_start"] >= m.start()), None)
    for t in turns:
        t["section"] = "qa" if (qa_start is not None and t["turn_index"] >= qa_start) else "prepared_remarks"

    # Analyst identification (structural + robust): in Q&A, the first non-operator speaker after
    # each operator hand-off is the analyst who was just introduced; everyone else answering is
    # management. This correctly catches execs who speak ONLY in Q&A (e.g. a segment CEO), which a
    # prepared-remarks roster would miss. Enrich analyst firms from the participant roster preamble.
    firms = _parse_participant_firms(turns)
    qa_analysts: set[str] = set()
    expect_analyst = False
    for t in turns:
        if t["section"] != "qa":
            continue
        if t["is_operator"]:
            expect_analyst = True
        elif expect_analyst and t["speaker_name"]:
            qa_analysts.add(t["speaker_name"])
            expect_analyst = False
    # Roster (firms) + structural (first-after-operator) — MINUS anyone who gave prepared remarks
    # (execs give prepared remarks; analysts never do). This removes execs who also answer in Q&A
    # (e.g. the CEO) from the analyst set even if a stray hand-off briefly tagged them.
    prepared_speakers = {t["speaker_name"] for t in turns
                         if t["section"] == "prepared_remarks" and t["speaker_name"] and not t["is_operator"]}
    analysts = (set(firms) | qa_analysts) - prepared_speakers

    last_analyst = None
    for t in turns:
        name = t["speaker_name"]
        if name is None:
            role = "other"
        elif t["is_operator"]:
            role = "operator"
        elif name in analysts:
            role = "analyst"
        else:
            role = "management"
        t["speaker_role"] = role
        t["speaker_firm"] = firms.get(name) if role == "analyst" else None
        t["is_question"] = role == "analyst" and "?" in t["utt"]
        t["replies_to_turn_index"] = None
        if t["section"] == "qa":
            if role == "analyst":
                last_analyst = t["turn_index"]
            elif role == "management" and last_analyst is not None:
                t["replies_to_turn_index"] = last_analyst
    return turns


def _parse_participant_firms(turns: list[dict]) -> dict[str, str]:
    """Analyst → firm map from the participant-roster preamble ('Name - Firm: Name - Firm: …')."""
    text = " ".join(t["verbatim_text"] for t in turns if t["speaker_name"] is None)
    firms: dict[str, str] = {}
    for chunk in re.split(r"[:;\n]", text):
        m = re.match(r"\s*([A-Z][A-Za-z.'’\-]+(?:\s+[A-Z][A-Za-z.'’\-]+){1,3})\s*[-–—]\s*(.+?)\s*$",
                     chunk.strip())
        if m and 1 <= len(m.group(2).split()) <= 5:
            firms[m.group(1).strip()] = m.group(2).strip()
    return firms


def _artifacts(db, rid, fy, fq):
    sql = ("SELECT content_sha256, source_uri, source_locator_json FROM raw_artifact "
           "WHERE artifact_kind='fmp_earnings_transcript'")
    rows = db.execute(text(sql)).fetchall()
    out = []
    for sha, uri, loc_json in rows:
        loc = json.loads(loc_json) if loc_json else {}
        sym = loc.get("symbol")
        r = db.execute(text("SELECT retailer_id FROM major_retailers WHERE ticker=:t"), {"t": sym}).fetchone()
        if not r:
            continue
        rr, yy, qq = r[0], loc.get("fiscal_year"), loc.get("fiscal_quarter")
        if (rid and rr != rid) or (fy and yy != fy) or (fq and qq != fq):
            continue
        out.append((rr, yy, qq, sha, uri))
    return out


def structure_quarter(db, store, rid, fy, fq, sha, uri) -> int:
    raw = store.get(sha)
    obj = json.loads(raw)
    rec = obj[0] if isinstance(obj, list) else obj
    content = rec.get("content") or ""
    if not content:
        logger.warning("empty content r%s FY%sQ%s", rid, fy, fq)
        return 0
    turns = structure_content(content)
    # invariant: turns tile the content exactly
    assert "".join(t["verbatim_text"] for t in turns) == content, "turn offsets do not reconstruct raw"
    ped = db.execute(text("SELECT period_end_date FROM retailer_financials WHERE retailer_id=:r "
                          "AND fiscal_year=:y AND fiscal_quarter=:q AND is_latest LIMIT 1"),
                     {"r": rid, "y": fy, "q": fq}).fetchone()
    period_end = ped[0] if ped else None

    db.execute(text("UPDATE transcript_turn SET is_latest=False, updated_at=now() WHERE "
                    "retailer_id=:r AND fiscal_year=:y AND fiscal_quarter=:q AND source='fmp' AND is_latest"),
               {"r": rid, "y": fy, "q": fq})
    for t in turns:
        db.add(TranscriptTurn(
            retailer_id=rid, fiscal_year=fy, fiscal_quarter=fq, period_end_date=period_end,
            source="fmp", content_sha256=sha, source_url=uri, turn_index=t["turn_index"],
            section=t["section"], speaker_name=t["speaker_name"], speaker_role=t["speaker_role"],
            speaker_firm=t.get("speaker_firm"),
            char_start=t["char_start"], char_end=t["char_end"], verbatim_text=t["verbatim_text"],
            word_count=t["word_count"], is_question=t["is_question"],
            replies_to_turn_index=t["replies_to_turn_index"], source_format="fmp", is_latest=True))
    db.commit()
    return len(turns)


def main() -> int:
    p = argparse.ArgumentParser(description="Build transcript_turn (L2) from raw L1")
    p.add_argument("--retailer-id", type=int)
    p.add_argument("--fy", type=int)
    p.add_argument("--fq", type=int)
    args = p.parse_args()
    db = SessionLocal()
    store = RawStore()
    try:
        arts = _artifacts(db, args.retailer_id, args.fy, args.fq)
        total_turns = total_q = 0
        for rid, fy, fq, sha, uri in sorted(arts):
            n = structure_quarter(db, store, rid, fy, fq, sha, uri)
            if n:
                total_q += 1
                total_turns += n
                logger.info("structured r%s FY%sQ%s -> %d turns", rid, fy, fq, n)
        print(f"\n✓ transcript_turn built: {total_turns} turns across {total_q} quarter(s)")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
