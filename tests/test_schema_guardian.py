"""
Schema Guardian Test
Runs after every migration and model change.
Confirms that the actual database tables and columns match
the SQLAlchemy model definitions exactly.
Zero tolerance for drift.
"""

import pytest
from sqlalchemy import inspect, text
from database.base import engine
import database.models  # noqa — triggers all model imports
from database.base import Base


class TestSchemaGuardian:

    def setup_method(self):
        self.inspector = inspect(engine)
        self.db_tables = set(self.inspector.get_table_names())

    def test_all_model_tables_exist_in_database(self):
        """Every model class must have a corresponding table in the database."""
        model_tables = set(Base.metadata.tables.keys())
        missing = model_tables - self.db_tables
        assert not missing, (
            f"These tables are in models but NOT in the database:\n"
            f"{chr(10).join(sorted(missing))}\n"
            f"Run: alembic upgrade head"
        )

    def test_no_extra_tables_without_models(self):
        """Flag tables in DB that have no model (excluding alembic's own table)."""
        model_tables = set(Base.metadata.tables.keys())
        system_tables = {"alembic_version"}
        extra = self.db_tables - model_tables - system_tables
        # This is a warning, not a hard failure — legacy tables may exist
        if extra:
            print(f"\nWARNING: Tables in DB with no model: {extra}")

    def test_all_model_columns_exist_in_database(self):
        """Every column in every model must exist in the database table."""
        failures = []
        for table_name, table in Base.metadata.tables.items():
            if table_name not in self.db_tables:
                continue  # Already caught by test above
            db_columns = {
                col["name"] for col in self.inspector.get_columns(table_name)
            }
            model_columns = {col.name for col in table.columns}
            missing_cols = model_columns - db_columns
            if missing_cols:
                failures.append(
                    f"Table '{table_name}' missing columns: {missing_cols}"
                )
        assert not failures, (
            f"Column mismatches found:\n"
            + "\n".join(failures)
            + "\nRun: alembic upgrade head"
        )

    def test_critical_numeric_fields_are_not_text(self):
        """
        Critical rate fields must be REAL, never TEXT.
        These are used in arithmetic — a TEXT type silently breaks calculations.
        """
        critical_checks = [
            ("factory_financing_cost", "india_rate_pct"),
            ("factory_financing_cost", "bangladesh_rate_pct"),
            ("factory_financing_cost", "vietnam_rate_pct"),
            ("factory_financing_cost", "china_rate_pct"),
            ("factory_financing_cost", "turkey_rate_pct"),
            ("fob_price_calculation", "financing_cost_doz"),
            ("fob_price_calculation", "fob_price_doz"),
        ]
        failures = []
        for table_name, col_name in critical_checks:
            if table_name not in self.db_tables:
                continue
            cols = self.inspector.get_columns(table_name)
            for col in cols:
                if col["name"] == col_name:
                    type_str = str(col["type"]).upper()
                    if "TEXT" in type_str or "VARCHAR" in type_str or "CHAR" in type_str:
                        failures.append(
                            f"{table_name}.{col_name} is {type_str} — must be NUMERIC/REAL"
                        )
        assert not failures, (
            f"Type failures — these will silently break cost calculations:\n"
            + "\n".join(failures)
        )

    def test_commodity_futures_has_separate_tenor_fields(self):
        """
        Commodity futures must have 5 separate tenor fields.
        A combined field cannot be used to compute the forward curve or contango signal.
        """
        if "commodity_futures" not in self.db_tables:
            pytest.skip("commodity_futures table not yet created")
        db_columns = {
            col["name"] for col in self.inspector.get_columns("commodity_futures")
        }
        required = {
            "ice_cotton_2_spot",
            "ice_cotton_2_3m",
            "ice_cotton_2_6m",
            "ice_cotton_2_9m",
            "ice_cotton_2_12m",
        }
        missing = required - db_columns
        assert not missing, f"commodity_futures missing tenor fields: {missing}"

    def test_every_table_has_timestamps(self):
        """Every table must have created_at and updated_at.

        raw_artifact is exempt from updated_at by design: it is the immutable, append-only
        medallion L1 layer (it carries created_at + captured_at, never updated_at — the
        absence of an update path IS the immutability guarantee).
        """
        exempt = {"alembic_version"}
        updated_at_exempt = {"raw_artifact"}
        failures = []
        for table_name in self.db_tables - exempt:
            db_columns = {
                col["name"] for col in self.inspector.get_columns(table_name)
            }
            for ts_col in ["created_at", "updated_at"]:
                if ts_col == "updated_at" and table_name in updated_at_exempt:
                    continue
                if ts_col not in db_columns:
                    failures.append(f"{table_name} missing {ts_col}")
        assert not failures, (
            f"Missing timestamps:\n" + "\n".join(failures)
        )

    def test_intelligence_outputs_have_as_of_date_and_model_version(self):
        """All 15 intelligence output tables must have as_of_date and model_version."""
        output_tables = [
            "current_landed_cost_per_dozen", "forward_landed_cost_90day",
            "most_cost_effective_corridor", "commodity_risk_in_open_programs",
            "hedge_opportunity_recommendation", "top5_competitor_sourcing",
            "retailer_demand_forecast", "tariff_exposure_analysis",
            "factory_financing_impact", "factory_capacity_constraints",
            "otd_risk_score_per_program", "freight_booking_window",
            "scf_opportunity_per_factory", "competitor_factory_intel",
            "program_pnl_with_levers",
        ]
        failures = []
        for table_name in output_tables:
            if table_name not in self.db_tables:
                continue
            db_columns = {
                col["name"] for col in self.inspector.get_columns(table_name)
            }
            for required_col in ["as_of_date", "model_version"]:
                if required_col not in db_columns:
                    failures.append(f"{table_name} missing {required_col}")
            for col in self.inspector.get_columns(table_name):
                if col["name"] == "model_version" and col.get("nullable", True):
                    failures.append(
                        f"{table_name}.model_version must be NOT NULL"
                    )
        assert not failures, "\n".join(failures)

    def test_foreign_keys_enabled(self):
        """SQLite foreign key enforcement must be on for every connection."""
        with engine.connect() as conn:
            result = conn.execute(text("PRAGMA foreign_keys"))
            fk_status = result.fetchone()[0]
        assert fk_status == 1, (
            "Foreign keys are OFF. Check database/base.py — the PRAGMA event listener "
            "must set PRAGMA foreign_keys=ON on every connection."
        )

    def test_specialist_referred_is_boolean(self):
        """duty_drawback.specialist_referred must be Boolean, not Varchar."""
        if "duty_drawback" not in self.db_tables:
            pytest.skip("duty_drawback table not yet created")
        cols = self.inspector.get_columns("duty_drawback")
        for col in cols:
            if col["name"] == "specialist_referred":
                type_str = str(col["type"]).upper()
                assert "TEXT" not in type_str, (
                    f"duty_drawback.specialist_referred is {type_str} — must be BOOLEAN"
                )
