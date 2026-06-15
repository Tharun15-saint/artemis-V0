from sqlalchemy import Boolean, Column, Integer, String, Numeric, Date, DateTime, Text
from sqlalchemy.sql import func
from database.base import Base


class HsCodes(Base):
    __tablename__ = "hs_codes"
    hs_code_id  = Column(Integer, primary_key=True)
    code        = Column(String(50))
    description = Column(String(255))
    created_at  = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at  = Column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)


class UsDutyRates(Base):
    __tablename__ = "us_duty_rates"
    duty_rate_id      = Column(Integer, primary_key=True)
    hs_code_id        = Column(Integer)
    ntr_rate          = Column(Numeric(8, 4))
    section_301_china = Column(String(50))
    gsp_status        = Column(String(50))
    source            = Column(String(255))
    created_at        = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at        = Column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)


class FreeTradeAgreements(Base):
    __tablename__ = "free_trade_agreements"
    fta_id                = Column(Integer, primary_key=True)
    agreement_name        = Column(String(255))
    beneficiary_countries = Column(String(255))
    duty_reduction_pct    = Column(Numeric(6, 2))
    yarn_forward_rule     = Column(String(255))
    created_at            = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at            = Column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)


class Uflpa(Base):
    __tablename__ = "uflpa"
    uflpa_id               = Column(Integer, primary_key=True)
    rebuttable_presumption = Column(String(255))
    border_block_risk      = Column(Numeric(5, 2))
    xinjiang_inputs        = Column(String(255))
    compliance_doc_cost    = Column(Numeric(10, 4))
    created_at             = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at             = Column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)


class EuCsddd(Base):
    __tablename__ = "eu_csddd"
    csddd_id                      = Column(Integer, primary_key=True)
    supply_chain_mapping_required = Column(String(255))
    verification_requirement      = Column(String(255))
    created_at                    = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at                    = Column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)


class DeMinimis(Base):
    __tablename__ = "de_minimis"
    de_minimis_id            = Column(Integer, primary_key=True)
    threshold_amount         = Column(Numeric(10, 2))
    duty_free_entry          = Column(String(50))
    regulatory_pressure_flag = Column(String(50))
    created_at               = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at               = Column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)


class UsDutyRateSchedule(Base):
    __tablename__ = "us_duty_rate_schedule"
    rate_id                   = Column(Integer, primary_key=True, autoincrement=True)
    hts_number                = Column(String(20), nullable=False)
    hts_description           = Column(Text)
    chapter                   = Column(Integer, nullable=False)
    heading                   = Column(String(10), nullable=False)
    indent_level              = Column(Integer)
    ntr_rate_pct              = Column(Numeric(8, 4))
    ntr_rate_text             = Column(String(100))
    ntr_rate_is_compound      = Column(Boolean, default=False)
    fta_free_countries        = Column(Text)
    jusfta_jordan_free        = Column(Boolean, default=False)
    korus_korea_free          = Column(Boolean, default=False)
    morocco_fta_free          = Column(Boolean, default=False)
    cafta_dr_free             = Column(Boolean, default=False)
    column2_rate_text         = Column(String(100))
    additional_duties_text    = Column(String(200))
    section_301_china_applies = Column(Boolean, default=False)
    section_301_china_rate_pct = Column(Numeric(8, 4))
    section_301_list          = Column(String(20))
    ieepa_universal_rate_pct  = Column(Numeric(8, 4), default=10.0)
    ieepa_universal_notes     = Column(Text)
    effective_date            = Column(Date, nullable=False)
    hts_revision              = Column(String(20), nullable=False)
    source                    = Column(String(100), nullable=False)
    data_source_url           = Column(String(500))
    last_verified             = Column(Date, nullable=False)
    is_latest                 = Column(Boolean, default=True)
    created_at                = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at                = Column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)


class UsDutyCountryEffectiveRate(Base):
    __tablename__ = "us_duty_country_effective_rate"
    effective_rate_id         = Column(Integer, primary_key=True, autoincrement=True)
    hts_number                = Column(String(20), nullable=False)
    origin_country            = Column(String(100), nullable=False)
    origin_iso2               = Column(String(2), nullable=False)
    ntr_rate_pct              = Column(Numeric(8, 4))
    fta_rate_pct              = Column(Numeric(8, 4))
    fta_program               = Column(String(50))
    section_301_additional_pct = Column(Numeric(8, 4))
    ieepa_additional_pct      = Column(Numeric(8, 4))
    effective_rate_pct        = Column(Numeric(8, 4), nullable=False)
    effective_rate_notes      = Column(Text)
    yarn_forward_required     = Column(Boolean, default=False)
    yarn_forward_met_assumption = Column(String(20), default="assumed_met")
    uflpa_risk                = Column(Boolean, default=False)
    as_of_date                = Column(Date, nullable=False)
    source                    = Column(String(100), nullable=False)
    is_latest                 = Column(Boolean, default=True)
    created_at                = Column(DateTime, server_default=func.now(), nullable=False)
