# Artemis Change Protocol
# Follow these exact steps for EVERY change. No exceptions.

## THE FIVE STEPS (memorise these)

1. BACKUP
   cp artemis.db artemis_backup_$(date +%Y%m%d_%H%M).db

2. UPDATE MASTER_SCHEMA.md
   Change the document first. The document is the source of truth.
   If you change code without updating this document, Cursor will
   work from stale context and contradict your changes.

3. UPDATE THE SQLALCHEMY MODEL
   Edit the model class in database/models/ to match the schema change.

4. GENERATE AND RUN THE MIGRATION
   alembic revision --autogenerate -m "brief description of change"
   Review the generated file in alembic/versions/
   alembic upgrade head

5. UPDATE .cursorrules IF BEHAVIOUR CHANGED
   If you added a new execution flow, a new signal rule, or a new
   output table — add it to .cursorrules so Cursor knows in every
   future session.

## Then verify
   pytest tests/test_schema_guardian.py -v
   All tests must pass before you write any new feature code.

## The backup rule
   Always. Every time. Before every migration.
   Backup takes 2 seconds. Recovery takes hours.

## Types of change

### Adding a new field (easiest)
   Edit MASTER_SCHEMA.md → edit model → alembic autogenerate → upgrade head
   SQLite: ALTER TABLE ADD COLUMN is safe and instant.

### Adding a new table
   Edit MASTER_SCHEMA.md → create new model class → alembic autogenerate → upgrade head

### Changing a field type (requires care)
   Edit MASTER_SCHEMA.md → edit model type → alembic autogenerate
   Review the generated migration — Alembic will use batch mode for SQLite
   which handles this safely via rename-create-copy-drop automatically.
   → upgrade head

### Removing a field (do rarely)
   Only remove when certain nothing reads that field.
   Edit MASTER_SCHEMA.md → edit model → alembic autogenerate → upgrade head
   Alembic batch mode handles this.

### Changing intelligence logic (no migration needed)
   Edit the function in intelligence/
   Update docs/DATA_SOURCES.md if a new source is added
   Update .cursorrules if a new signal rule is added
   No migration needed — intelligence logic is not in the database.

### Adding a new partner integration
   Add partner entity to MASTER_SCHEMA.md
   Add model class → alembic autogenerate → upgrade head
   Add execution flow to docs/ARCHITECTURE.md and .cursorrules
   Create execution/{partner}.py

## What NEVER to do
   Never edit artemis.db directly with a SQLite browser
   Never change a model without updating MASTER_SCHEMA.md first
   Never run migrations without backing up first
   Never skip the schema guardian test after a change
   Never add fields to models that are not in MASTER_SCHEMA.md
