# Backend utility targets. See MIGRATIONS.md for the full playbook.
#
# Usage:
#   cd backend && source venv/bin/activate
#   make db-current
#   make db-revision MSG="add foo table"
#   make db-upgrade
#
# Prod commands require ALEMBIC_ALLOW_PROD=1 (safety rail in alembic/env.py):
#   ALEMBIC_ALLOW_PROD=1 make db-upgrade
#
# All commands use settings.DATABASE_URL. Override for the shell with:
#   export DATABASE_URL="postgresql+asyncpg://..."

PY ?= python

.PHONY: help db-current db-history db-heads db-revision db-upgrade db-downgrade \
        db-upgrade-dry db-stamp-head db-check

help:
	@echo "Alembic targets:"
	@echo "  make db-current         Show current DB revision"
	@echo "  make db-history         List all migrations in order"
	@echo "  make db-heads           Show current head revision(s) in /versions"
	@echo "  make db-revision MSG=.. Autogenerate a new migration from model diff"
	@echo "  make db-upgrade         Apply pending migrations"
	@echo "  make db-upgrade-dry     Emit SQL to stdout WITHOUT running (safe dry run)"
	@echo "  make db-downgrade       Revert the most recent migration"
	@echo "  make db-stamp-head      Mark DB as at head WITHOUT running any SQL"
	@echo "  make db-check           Verify models match latest migration (CI helper)"
	@echo ""
	@echo "Prod operations require: ALEMBIC_ALLOW_PROD=1"

db-current:
	$(PY) -m alembic current

db-history:
	$(PY) -m alembic history --verbose

db-heads:
	$(PY) -m alembic heads

db-revision:
	@if [ -z "$(MSG)" ]; then \
		echo "Usage: make db-revision MSG=\"short description\""; exit 1; \
	fi
	$(PY) -m alembic revision --autogenerate -m "$(MSG)"
	@echo ""
	@echo ">>> REVIEW THE GENERATED FILE BEFORE COMMITTING. See MIGRATIONS.md §'Autogen gotchas'."

db-upgrade:
	$(PY) -m alembic upgrade head

db-upgrade-dry:
	$(PY) -m alembic upgrade head --sql

db-downgrade:
	$(PY) -m alembic downgrade -1

db-stamp-head:
	$(PY) -m alembic stamp head

# CI helper: succeeds if models and latest migration agree (no pending changes).
# Fails loud if you committed a model change without generating a migration.
db-check:
	$(PY) -m alembic check
