from sqlalchemy import Boolean, Column, Date, DateTime, Integer, Numeric, String, Text
from sqlalchemy.sql import func
from database.base import Base


class ActorRelationship(Base):
    """
    World 2: The relationship between any two actors in the supply chain.

    The RRK (manufacturer) × Classic Fashion/Athlux Studio (importer) pair is the single most
    important row in this table — it encodes 30+ years of collaboration history. The health_score
    and payment_behavior_score are the primary signals that affect pricing latitude, payment terms
    flexibility, and tolerance for spec changes.

    power_dynamic determines who absorbs cost shocks. In the RRK × Classic Fashion relationship,
    the importer has historically been buyer_dominant — this means RRK absorbs material cost spikes
    more often than it passes them through. This pattern is itself a LearnedCoefficient.

    actor_a and actor_b are typed polymorphically — the pair can be manufacturer×importer,
    importer×retailer, manufacturer×supplier, or person×actor.
    """
    __tablename__ = "actor_relationship"

    relationship_id             = Column(Integer, primary_key=True)
    actor_a_id                  = Column(Integer, nullable=False)
    actor_a_type                = Column(String(50), nullable=False)
    # manufacturer | importer | retailer | supplier | person
    actor_b_id                  = Column(Integer, nullable=False)
    actor_b_type                = Column(String(50), nullable=False)
    relationship_type           = Column(String(50), nullable=False)
    # manufacturer_importer | importer_retailer | manufacturer_supplier | person_actor
    relationship_start_date     = Column(Date)
    is_active                   = Column(Boolean, default=True, nullable=False)
    volume_tier                 = Column(String(50))
    # primary | major | secondary | occasional | former
    power_dynamic               = Column(String(50))
    # buyer_dominant | balanced | supplier_dominant
    annual_volume_units         = Column(Integer)
    annual_volume_usd           = Column(Numeric(14, 2))
    payment_behavior_score      = Column(Numeric(4, 3))     # 0–1 (1.0 = always pays on time)
    quality_dispute_count       = Column(Integer, default=0)
    price_negotiation_pattern   = Column(String(100))
    # price_first | quality_first | relationship_first | speed_first | combined
    health_score                = Column(Numeric(4, 3))     # 0–1 composite
    # health_score_components: JSON e.g. {"payment": 0.9, "quality": 0.85, "communication": 0.8}
    health_score_components_json = Column(Text)
    payment_days_avg            = Column(Integer)           # average actual payment cycle (days)
    # notable_events: JSON array of key events e.g. [{"date": "2020-04", "event": "covid_pause"}]
    notable_events_json         = Column(Text)
    relationship_notes          = Column(Text)
    created_at                  = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at                  = Column(DateTime, server_default=func.now(),
                                         onupdate=func.now(), nullable=False)


class Person(Base):
    """
    World 2: A specific individual with a role and decision authority within an actor entity.

    Connects to EmailMessage.sender_person_id and CommunicationThread — enabling attribution
    of specific decisions, prices agreed, and sentiments to named individuals.

    decision_style is a learned field, calibrated from communication pattern analysis:
    fast_decisive people approve samples quickly and rarely re-open settled prices;
    approval_seeking people escalate even minor decisions to their superiors.

    role_end_date=NULL means the person is currently in this role.
    """
    __tablename__ = "person"

    person_id                   = Column(Integer, primary_key=True)
    full_name                   = Column(String(255), nullable=False)
    email_address               = Column(String(255))
    phone_number                = Column(String(50))
    actor_id                    = Column(Integer)
    actor_type                  = Column(String(50))
    # manufacturer | importer | retailer | supplier
    role                        = Column(String(50))
    # buyer | pdm | sourcing_manager | production_manager | quality_manager
    # finance_manager | logistics_manager | ceo | director | agent | owner
    decision_authority          = Column(String(50))
    # final_approver | recommender | executor | informational_only
    role_start_date             = Column(Date)
    role_end_date               = Column(Date, nullable=True)
    is_active                   = Column(Boolean, default=True)
    decision_style              = Column(String(50))
    # fast_decisive | deliberate | approval_seeking | escalation_prone | consensus_builder
    communication_language_pref = Column(String(20))    # en | hi | ta | ar
    notes                       = Column(Text)
    created_at                  = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at                  = Column(DateTime, server_default=func.now(),
                                         onupdate=func.now(), nullable=False)
