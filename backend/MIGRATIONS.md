# Database Migrations

Schema changes to this app are managed by **Alembic**. The app itself no longer
mutates the schema at startup beyond a safety-net `create_all` for fresh local
databases — every real change goes through a reviewable migration file.

This doc is the **playbook**. Read it end-to-end the first time. Bookmark the
"Common tasks" section for day-to-day use.

---

## Layout

```
backend/
├── alembic.ini                        # Alembic config. URL is blank here;
│                                      # injected at runtime from settings.DATABASE_URL
├── alembic/
│   ├── env.py                         # Wired to app.config + app.database.Base
│   ├── script.py.mako                 # Template for new migration files
│   └── versions/
│       └── 2026_04_23_<rev>_baseline.py
│                                      # One-time snapshot of the schema as
│                                      # it existed when Alembic was introduced.
│                                      # Never executed against prod — see file header.
├── app/
│   ├── database.py                    # Engine, Base, init_db()
│   └── models.py                      # All SQLAlchemy models — add new tables here
└── MIGRATIONS.md                      # This file
```

---

## Concepts in one minute

A **migration** is a Python file in `alembic/versions/` describing one schema
change. It has an `upgrade()` (apply) and `downgrade()` (revert). Files are
linked in a chain (each knows its `down_revision`). Alembic tracks the current
DB revision in a single-row table called `alembic_version`.

- `alembic upgrade head` → apply every pending migration up to the latest
- `alembic downgrade -1` → revert one migration
- `alembic current` → which revision is this DB at?
- `alembic history` → all migrations in order
- `alembic revision --autogenerate -m "msg"` → diff models vs DB, generate a file
- `alembic stamp head` → mark DB as at head WITHOUT running anything (used
  when the DB already has the schema but no `alembic_version` row yet)

---

## Environment setup

Migrations use `app.config.settings.DATABASE_URL` — same source the app uses.
Point Alembic at the right DB by setting the env var:

```bash
# Local SQLite (default — works out of the box)
export DATABASE_URL="sqlite+aiosqlite:///./stock_research.db"

# Production Cloud SQL Postgres (via Cloud SQL Auth Proxy — see below)
export DATABASE_URL="postgresql+asyncpg://USER:PASS@127.0.0.1:5432/DBNAME"
```

Commands run from `backend/`:

```bash
cd backend
source venv/bin/activate
make db-current     # or: python -m alembic current
```

### Connecting your laptop to prod Cloud SQL

Don't expose Cloud SQL to the public internet. Use the **Cloud SQL Auth
Proxy** — a binary that opens a local TCP port forwarding to Cloud SQL over
an IAM-authenticated connection.

```bash
# 1. Install the proxy (one time)
brew install cloud-sql-proxy     # macOS
# or: https://cloud.google.com/sql/docs/postgres/sql-proxy

# 2. Authenticate gcloud (one time)
gcloud auth application-default login

# 3. Start the proxy (leave it running in a separate terminal)
cloud-sql-proxy --port 5432 PROJECT_ID:REGION:INSTANCE_NAME

# 4. In your migration terminal, point DATABASE_URL at 127.0.0.1
export DATABASE_URL="postgresql+asyncpg://APP_USER:PASSWORD@127.0.0.1:5432/DBNAME"

# 5. Verify you're pointed at the right DB before any destructive op
make db-current
```

> **Tip:** Put your prod `DATABASE_URL` in a separate shell profile or a
> `.env.prod` file you source explicitly — never in `.env`, which is read
> by the app on every run.

---

## Common tasks

### 1. Check the current revision
```bash
make db-current
```

### 2. Add a new table or column
```bash
# a) Edit app/models.py — add/change the model.
# b) Autogenerate the migration file:
make db-revision MSG="add trade_transaction table"
# → alembic/versions/2026_04_24_<rev>_add_trade_transaction_table.py

# c) OPEN THE FILE AND REVIEW IT. See "Autogen gotchas" below.

# d) Dry-run the SQL without touching the DB:
make db-upgrade-dry > /tmp/migration.sql
less /tmp/migration.sql

# e) Apply to your local DB:
make db-upgrade

# f) Commit BOTH the model change and the migration file together.
```

### 3. Apply pending migrations
```bash
make db-upgrade        # local: just runs
ALEMBIC_ALLOW_PROD=1 make db-upgrade   # prod: explicit opt-in required
```

### 4. Undo the last migration
```bash
make db-downgrade      # reverts one revision
```

### 5. Stamp an existing DB as being at head
Used once per environment when introducing Alembic to a DB that already has
the schema (prod, long-running local dev DBs). No SQL runs; only the
`alembic_version` row is written.

```bash
# Local
make db-stamp-head

# Prod (opt-in required)
ALEMBIC_ALLOW_PROD=1 make db-stamp-head
```

---

## Autogen gotchas — always review the generated file

Alembic's `--autogenerate` is ~90% correct. The following it gets **wrong**
or **cannot see** — you must edit the migration by hand in these cases:

| Situation                            | What autogen does           | What you should do                                    |
|--------------------------------------|-----------------------------|-------------------------------------------------------|
| Rename column/table                  | Generates drop + add        | Replace with `op.alter_column(..., new_column_name=)` |
| Change Postgres enum values          | Silently misses             | Write `op.execute("ALTER TYPE ... ADD VALUE ...")`    |
| Rename a constraint/index            | Drop + recreate             | Use `op.execute("ALTER INDEX ... RENAME TO ...")`     |
| `server_default` expression changes  | May or may not detect       | Verify the diff matches your intent                   |
| Data backfill for a new NOT NULL col | Generates `NOT NULL` upfront — will fail on existing rows | Split: add nullable → backfill → alter to NOT NULL |
| Charset/collation differences        | Noisy phantom diffs         | Delete the spurious lines                             |
| Dropping columns with data           | Generates drop (data gone)  | Add a safety delay or confirm the data is truly dead  |

**Rule: every generated migration gets human-reviewed before commit. No
exceptions.**

---

## Data migrations (moving/transforming rows, not schema)

For anything that manipulates data (backfills, column splits, enum remaps),
use `op.execute("SQL...")` or `op.bulk_insert(...)`. **Do NOT use the ORM** —
models reflect the *current* code, which may not match the migration's
point-in-time schema. Raw SQL is the safe choice in a migration.

```python
def upgrade():
    op.add_column("saved_strategies", sa.Column("analysis_basis", sa.String(20)))
    # Backfill: every existing row gets "technical" as a default
    op.execute("UPDATE saved_strategies SET analysis_basis = 'technical' WHERE analysis_basis IS NULL")
    op.alter_column("saved_strategies", "analysis_basis", nullable=False)
```

---

## Non-negotiable rules

1. **Never edit an applied migration.** If prod has run it, it's frozen.
   Fix forward with a new migration.
2. **Every migration gets human-reviewed** before commit. Autogen is a
   suggestion, not a truth.
3. **Destructive migrations need a working `downgrade()`** even if data
   will be lost — so an old app version can boot against the reverted schema.
4. **`ALEMBIC_ALLOW_PROD=1` is required** for any write operation against
   prod. The safety rail in `env.py` refuses without it.
5. **Commit model + migration together.** A model change without its
   migration will break CI and confuse the next engineer.
6. **Always take a snapshot before running against prod.** GCP Cloud SQL has
   point-in-time recovery — use it. One click in the Cloud SQL console.
7. **Data migrations use `op.execute` / `op.bulk_insert`**, never the ORM.

---

## First-time setup for a new environment

### A brand-new dev laptop (empty SQLite)
```bash
cd backend && source venv/bin/activate
export DATABASE_URL="sqlite+aiosqlite:///./stock_research.db"
make db-upgrade        # creates every table from scratch via the baseline migration
```

### An existing dev laptop (SQLite already has some tables from old `create_all`)
```bash
# Your DB already has the schema. Stamp it at head so future upgrades work.
make db-stamp-head
```

### Production Cloud SQL (already populated — one-time operation)
```bash
# With Cloud SQL Auth Proxy running and DATABASE_URL exported:
make db-current                            # → expect: (none), no alembic_version table yet
ALEMBIC_ALLOW_PROD=1 make db-stamp-head    # writes a single row, no schema changes
make db-current                            # → expect: the baseline revision ID
```

After that, future prod migrations are `ALEMBIC_ALLOW_PROD=1 make db-upgrade`.

---

## Troubleshooting

**"Target database is not up to date"**  
Your DB is behind the migration files. Run `make db-upgrade`.

**"Can't locate revision identified by X"**  
The `alembic_version` row points at a revision file that's missing from
`alembic/versions/`. Either recover the file from git, or `alembic stamp <rev>`
to the correct one (only if you know what you're doing).

**Startup log says "no alembic_version row found"**  
The DB has no Alembic tracking. For an existing populated DB, run
`make db-stamp-head`. For an empty one, `make db-upgrade`.

**Startup log says "DB is at revision X but code expects Y"**  
Schema is out of sync with the code. Either:
- Pull the latest code + run `make db-upgrade`, or
- Downgrade the app image to match the DB.

**Autogen generates phantom diffs (e.g. server_default formatting)**  
Normally harmless but noisy — delete the lines before committing. If the
noise repeats every autogen run, adjust the model's `server_default` to
match what Postgres reports back.

**"Refusing to run Alembic against what looks like a production database"**  
You're pointed at prod without `ALEMBIC_ALLOW_PROD=1`. That's the safety
rail. If you really mean to run, `export ALEMBIC_ALLOW_PROD=1`.

---

## FAQ

**Why keep `create_all` in `init_db()` if Alembic manages schema?**  
As a safety net for fresh dev laptops so a new contributor can `git clone`
and run the app without a migration step. On populated DBs it's a no-op.
New schema work still goes through migrations.

**Can I just add a column with raw SQL on prod and move on?**  
Don't. It creates drift — the model knows about the column, Alembic doesn't.
The next autogen run will try to "add" the column and fail on prod.

**What if I made a mistake in a migration after it ran locally but before
merging?**  
Still okay to edit. It only becomes frozen once it's applied in prod.
Delete the row from `alembic_version`, `git rm` the bad file, regenerate.
