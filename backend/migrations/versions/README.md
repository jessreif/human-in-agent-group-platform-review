# Database migrations

This directory holds the Alembic migrations for the application's database. Each file changes the schema in one step, and running them in order builds the schema from empty. Read this before adding a migration.

There are 17 migrations, from `0001_core` (`20260929_0001_core_persistence.py`) to `0017_mixed_arm` (`20261018_0017_mixed_arm.py`). The order is set by each file's `revision` and `down_revision` values, not by the date in its filename. For example, `20261016_0012_ai_qa.py` comes before `20261014_0013_qa_provenance.py`.

Keep exactly one head: every new migration must name the current last revision as its `down_revision`, so the chain never branches. The API reports itself not ready if the database's migration head differs from the one the build expects.
