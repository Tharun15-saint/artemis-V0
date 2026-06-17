"""
Parser regression tests for earnings_transcript_ingestion.py.

These run at PARSE LEVEL — zero API calls, zero cost, fully deterministic. They use
minimal hand-crafted snippets (not full transcripts) that reproduce the exact
structures we saw in real fool.com and Insider Monkey transcripts, including the
two bugs we fixed:
  - IM "bare" format (speaker on its own line) parsing to 0 passages, and
  - a Q&A-only executive (Furner) being misclassified as an analyst.

Count thresholds are scaled to the snippets; the full-transcript counts
(>50 passages, >=5 analysts) are verified by the live backfill verification, not here.

Run: python -m pytest tests/test_transcript_parser.py -v
"""

from data.ingestion.earnings_transcript_ingestion import parse_transcript

_EXEC_NAMES = {
    "John Furner", "Doug McMillon", "John Rainey", "Chris Nicholas",
    "Michael Fiddelke", "Brian Cornell", "Richard Gomez", "James Lee",
}

# ── fool.com: "Name -- Title" headers on their own lines, em-dash participants ──
FOOL_SNIPPET = """\
CALL PARTICIPANTS
President and CEO — John Furner
Executive Vice President and CFO — John Rainey
John Furner -- President and CEO
Thanks, everyone, and good morning. We delivered a strong quarter with fashion
comps up mid-single digits. Apparel led general merchandise this period.
John Rainey -- Executive Vice President and CFO
Gross margin expanded year over year. We continue investing in price for customers.
Operator
[Operator Instructions] Our first question comes from the line of Simeon Gutman with Morgan Stanley.
Simeon Gutman -- Morgan Stanley
Thanks for taking my question. Can you talk about apparel margins into the back half?
And how are you thinking about open-to-buy in softlines?
John Rainey -- Executive Vice President and CFO
Simeon, thanks for the question. Apparel margins improved on lower markdowns.
We expect continued discipline on inventory through the back half.
"""

# ── Insider Monkey INLINE (late-2025+): "Name: text" on one line ──
IM_INLINE_SNIPPET = """\
Target Corporation (NYSE:
TGT
) Q3 2025 Earnings Call Transcript November 19, 2025
Operator: Greetings. A question-and-answer session will follow the prepared remarks.
Michael Fiddelke: Thanks, and good morning. Our comps were down modestly this quarter.
Apparel remained soft but we like our holiday assortment.
Operator: Our first question comes from Simeon Gutman with Morgan Stanley.
Simeon Gutman: Thanks for the question. How should we think about the Q4 comp guide?
And what does it imply for discretionary categories like apparel?
Michael Fiddelke: Thanks, Simeon. We embedded a cautious discretionary assumption.
Apparel sell-through is the swing factor into the holiday.
"""

# ── Insider Monkey BARE (pre mid-2025): "Name:" on its own line, text follows ──
# Furner answers in Q&A but gives NO prepared remarks (McMillon + Rainey do) — the
# exact case that used to misclassify him as an analyst. Operator says "Joe Feldman";
# the speaker line is "Joseph Feldman" — must match by last name.
IM_BARE_SNIPPET = """\
Walmart Inc. (NYSE:
WMT
) Q1 2026 Earnings Call Transcript May 15, 2025
Operator:
Greetings. A question-and-answer session will follow the prepared remarks.
Doug McMillon:
Thanks, and good morning. We had a strong start with healthy comps.
General merchandise improved and eCommerce grew double digits.
John Rainey:
Margins were solid and we continue investing in price for our customers.
Inventory is well positioned heading into the next quarter.
Operator:
Our first question comes from the line of Joe Feldman with Telsey Advisory Group.
Joseph Feldman:
Thanks for taking my question. How are you managing tariff cost pressure?
And what does that mean for apparel sourcing and FOB pricing?
John Furner:
Joe, thanks for the question. We are managing tariffs through diversified sourcing.
Apparel costs are stable and we are protecting price for customers.
"""


def _by_role(passages):
    analysts = [p for p in passages if p.is_analyst_pressure]
    prepared = [p for p in passages if p.section == "prepared_remarks"]
    qa = [p for p in passages if p.section == "qa"]
    return analysts, prepared, qa


def test_motley_fool_qa_split():
    """fool.com must split prepared vs Q&A; analysts flagged, execs never."""
    p = parse_transcript(FOOL_SNIPPET, source_format="motley_fool")
    analysts, prepared, qa = _by_role(p)
    assert prepared, "no prepared-remarks passages parsed"
    assert qa, "Q&A boundary not detected — everything stayed prepared"
    assert any(x.is_analyst_pressure for x in qa), "no analyst flagged in Q&A"
    assert not any(x.is_analyst_pressure for x in prepared), "analyst flagged in prepared remarks"
    assert not any(x.speaker_name in _EXEC_NAMES for x in analysts), "executive flagged as analyst"


def test_insider_monkey_inline_format():
    """IM inline format parses; no junk speakers; exec prepared, analyst in Q&A."""
    p = parse_transcript(IM_INLINE_SNIPPET, source_format="insider_monkey")
    analysts, prepared, qa = _by_role(p)
    assert len(p) >= 3, "inline transcript parsed too few passages"
    assert "Subscribe" not in {x.speaker_name for x in p}, "junk 'Subscribe' speaker present"
    assert any(x.speaker_name == "Michael Fiddelke" for x in prepared), "Fiddelke missing from prepared"
    assert any(x.is_analyst_pressure for x in qa), "no analyst in Q&A"
    assert not any(x.speaker_name in _EXEC_NAMES for x in analysts), "executive flagged as analyst"


def test_insider_monkey_bare_format():
    """IM bare format (speaker on own line) must parse, not yield 0 passages."""
    p = parse_transcript(IM_BARE_SNIPPET, source_format="insider_monkey")
    analysts, prepared, qa = _by_role(p)
    assert len(p) >= 3, "BARE format parsed to ~0 passages — the original bug"
    assert "Subscribe" not in {x.speaker_name for x in p}, "junk 'Subscribe' speaker present"
    # Rainey/McMillon/Furner must never be analysts.
    flagged_execs = {x.speaker_name for x in analysts} & _EXEC_NAMES
    assert not flagged_execs, f"executives flagged as analyst: {flagged_execs}"
    assert any(x.is_analyst_pressure for x in qa), "no analyst flagged in Q&A"


def test_exec_not_flagged_as_analyst_when_absent_from_prepared():
    """The Furner bug: a CEO answering Q&A without prepared remarks is NOT an analyst."""
    p = parse_transcript(IM_BARE_SNIPPET, source_format="insider_monkey")
    furner = [x for x in p if x.speaker_name == "John Furner"]
    assert furner, "Furner not parsed at all"
    assert all(x.speaker_role == "management" for x in furner), "Furner not classified management"
    assert not any(x.is_analyst_pressure for x in furner), "Furner wrongly flagged analyst_pressure"


def test_operator_intro_name_variants():
    """Operator says 'Joe Feldman'; speaker line is 'Joseph Feldman' — match by last name."""
    p = parse_transcript(IM_BARE_SNIPPET, source_format="insider_monkey")
    feldman = [x for x in p if x.speaker_name == "Joseph Feldman"]
    assert feldman, "Joseph Feldman not parsed"
    assert all(x.is_analyst_pressure for x in feldman), "Joseph Feldman not flagged analyst (last-name match failed)"
    # No exec should be a false-positive analyst from the exec-in-Q&A pattern.
    analysts = {x.speaker_name for x in p if x.is_analyst_pressure}
    assert not (analysts & _EXEC_NAMES), f"false-positive exec analysts: {analysts & _EXEC_NAMES}"
