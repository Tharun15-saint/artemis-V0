from sqlalchemy import Boolean, Column, Date, DateTime, Integer, Numeric, String, Text
from sqlalchemy.sql import func
from database.base import Base


class CommunicationThread(Base):
    """
    World 6: A thread of related communications — an email chain, WhatsApp thread, meeting, or call.

    The 30-year RRK × Classic Fashion email corpus is the primary training dataset for the
    intelligence layer. Every email ever sent between these two actors is a thread of one or
    more EmailMessages. This table is the container; EmailMessage holds the actual content.

    communication_type is a structured classification of the thread's purpose — it enables
    the model to learn: "price negotiations that start post-sealing resolve 80% of the time
    with the manufacturer conceding 0.05–0.15 USD/dozen."

    key_signals_json is AI-extracted structured data from the thread:
    e.g. {"price_mentioned_usd": 3.95, "delivery_date_mentioned": "2022-08-15", "tone": "tense"}

    program_impact must be set when the thread is linked to a program — it categorizes
    the outcome of the conversation for training purposes.
    """
    __tablename__ = "communication_thread"

    thread_id                   = Column(Integer, primary_key=True)
    channel                     = Column(String(50), nullable=False)
    # email | zoom_transcript | whatsapp | in_person_meeting_notes | phone_call_notes
    subject_line                = Column(Text)
    program_id                  = Column(Integer, nullable=True)    # FK → program
    construction_id             = Column(Integer, nullable=True)    # FK → garment_construction
    communication_type          = Column(String(100))
    # tech_pack_transmittal | price_inquiry | price_negotiation | price_confirmation
    # sample_feedback | spec_change | delivery_update | quality_dispute
    # payment_follow_up | relationship_maintenance | order_cancellation | escalation
    initiated_by_actor_id       = Column(Integer)
    initiated_by_actor_type     = Column(String(50))
    thread_start_date           = Column(Date)
    thread_end_date             = Column(Date, nullable=True)
    message_count               = Column(Integer, default=0)
    resolution_days             = Column(Integer, nullable=True)
    outcome                     = Column(String(100))
    # resolved_mutual_agreement | resolved_manufacturer_conceded | resolved_buyer_conceded
    # resolved_escalation | unresolved | ongoing | informational_only
    # key_signals: JSON e.g. {"price_usd": 3.95, "delivery_date": "2022-08-15", "tone": "tense"}
    key_signals_json            = Column(Text)
    program_impact              = Column(String(50))
    # no_impact | cost_impact | timeline_impact | quality_impact | relationship_impact | multiple
    created_at                  = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at                  = Column(DateTime, server_default=func.now(),
                                         onupdate=func.now(), nullable=False)


class EmailMessage(Base):
    """
    World 6: A single email within a communication thread.

    body_text is stored VERBATIM — never summarized, never truncated. The raw text is the
    training signal. Any summarization or extraction belongs in separate derived fields,
    never in body_text itself.

    response_lag_hours measures time between this message and the previous one in the thread.
    Long lags on the buyer side during sample feedback rounds are a leading indicator of
    programme delay. Long lags on the manufacturer side during price negotiations signal
    that they are seeking internal approval — a power dynamic signal.

    sentiment_signal is AI-extracted: 'tense' threads that eventually resolve with
    manufacturer_conceded outcome calibrate the relationship model.

    contains_explicit_decision=True flags emails where a price, spec, or date was definitively
    agreed — these are the most valuable training examples for the decision extraction model.
    """
    __tablename__ = "email_message"

    message_id                  = Column(Integer, primary_key=True)
    thread_id                   = Column(Integer, nullable=False)   # FK → communication_thread
    sequence_in_thread          = Column(Integer)
    sender_person_id            = Column(Integer, nullable=True)    # FK → person (null until resolved)
    sender_email_raw            = Column(String(255))               # raw "from" for entity matching
    # recipient_person_ids: JSON array e.g. [1, 3, 7]
    recipient_person_ids_json   = Column(Text)
    # cc_person_ids: JSON array
    cc_person_ids_json          = Column(Text)
    sent_at                     = Column(DateTime, nullable=False)
    body_text                   = Column(Text)                      # VERBATIM — never modify
    language_detected           = Column(String(10))                # en | hi | ta | ar
    # attachments: JSON array e.g. [{"filename": "tech_pack_v2.pdf", "type": "tech_pack"}]
    attachments_json            = Column(Text)
    message_intent              = Column(String(50))
    # request | response | approval | rejection | revision_request | acknowledgment
    # escalation | information_share | complaint | clarification
    sentiment_signal            = Column(String(50))
    # cooperative | neutral | tense | frustrated | urgent | formal_diplomatic | informal_cordial
    response_lag_hours          = Column(Numeric(8, 2), nullable=True)
    contains_explicit_decision  = Column(Boolean, default=False)
    extraction_model            = Column(String(100), nullable=True)
    human_verified              = Column(Boolean, default=False)
    created_at                  = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at                  = Column(DateTime, server_default=func.now(),
                                         onupdate=func.now(), nullable=False)
