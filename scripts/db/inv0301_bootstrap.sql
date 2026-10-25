-- INV-0301 — Investigators database and roles on the host PostgreSQL 15 cluster.
-- Run as the postgres superuser:  sudo -u postgres psql
-- Passwords are set interactively with \password (SCRAM hash; nothing in files or history).
-- Idempotence: not idempotent; step 0 shows whether anything already exists.

-- 0. Pre-checks (expect scram-sha-256, and no rows for the next two queries)
SHOW password_encryption;
SELECT rolname FROM pg_roles WHERE rolname LIKE 'investigators%';
SELECT datname FROM pg_database WHERE datname LIKE 'investigators%';

-- 1. Roles: migrator owns the schema; runtime, research and compensation are separate logins
CREATE ROLE investigators_migrator     LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION CONNECTION LIMIT 3;
CREATE ROLE investigators_user         LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION CONNECTION LIMIT 40;
CREATE ROLE investigators_research     LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION CONNECTION LIMIT 5;
CREATE ROLE investigators_compensation LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION CONNECTION LIMIT 3;
\password investigators_migrator
\password investigators_user
\password investigators_research
\password investigators_compensation

-- 2. Database, closed to PUBLIC
CREATE DATABASE investigators_db OWNER investigators_migrator ENCODING 'UTF8' TEMPLATE template0;
REVOKE ALL ON DATABASE investigators_db FROM PUBLIC;
GRANT CONNECT ON DATABASE investigators_db TO investigators_user, investigators_research, investigators_compensation;

-- 3. Schemas and default privileges (apply to tables/sequences Alembic creates as investigators_migrator)
\c investigators_db
REVOKE ALL ON SCHEMA public FROM PUBLIC;
CREATE SCHEMA app      AUTHORIZATION investigators_migrator;
CREATE SCHEMA identity AUTHORIZATION investigators_migrator;

GRANT USAGE ON SCHEMA app      TO investigators_user, investigators_research, investigators_compensation;
GRANT USAGE ON SCHEMA identity TO investigators_user, investigators_compensation;

-- runtime: read/write research tables; identity is insert-only (cannot read platform links)
ALTER DEFAULT PRIVILEGES FOR ROLE investigators_migrator IN SCHEMA app      GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES    TO investigators_user;
ALTER DEFAULT PRIVILEGES FOR ROLE investigators_migrator IN SCHEMA app      GRANT USAGE, SELECT                  ON SEQUENCES TO investigators_user;
ALTER DEFAULT PRIVILEGES FOR ROLE investigators_migrator IN SCHEMA identity GRANT INSERT                         ON TABLES    TO investigators_user;
ALTER DEFAULT PRIVILEGES FOR ROLE investigators_migrator IN SCHEMA identity GRANT USAGE                          ON SEQUENCES TO investigators_user;
-- research: read-only research tables, no identity
ALTER DEFAULT PRIVILEGES FOR ROLE investigators_migrator IN SCHEMA app      GRANT SELECT                         ON TABLES    TO investigators_research;
-- compensation: read-only identity; access to entitlement/result tables is granted table-by-table by migrations
ALTER DEFAULT PRIVILEGES FOR ROLE investigators_migrator IN SCHEMA identity GRANT SELECT                         ON TABLES    TO investigators_compensation;

-- per-role session settings in this database
ALTER ROLE investigators_migrator     IN DATABASE investigators_db SET search_path = app, identity;
ALTER ROLE investigators_user         IN DATABASE investigators_db SET search_path = app, identity;
ALTER ROLE investigators_user         IN DATABASE investigators_db SET idle_in_transaction_session_timeout = '60s';
ALTER ROLE investigators_research     IN DATABASE investigators_db SET search_path = app;
ALTER ROLE investigators_research     IN DATABASE investigators_db SET default_transaction_read_only = on;
ALTER ROLE investigators_compensation IN DATABASE investigators_db SET search_path = identity, app;
ALTER ROLE investigators_compensation IN DATABASE investigators_db SET default_transaction_read_only = on;

-- 4. Verify
\du investigators*
\l investigators_db
\dn+
\ddp
-- pg_hba rules that can admit the Docker bridge (containers reach the host via host.docker.internal)
SELECT line_number, type, database, user_name, address, netmask, auth_method
FROM pg_hba_file_rules
WHERE type LIKE 'host%' AND (database::text ~ 'all|investigators' );

-- 5. OPTIONAL — host test database. Not needed for current tests (scripts/test_backend.sh uses a
--    throwaway container). Only create if you want integration tests against the host cluster.
-- \c postgres
-- CREATE ROLE investigators_test LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION CONNECTION LIMIT 5;
-- \password investigators_test
-- CREATE DATABASE investigators_test_db OWNER investigators_test ENCODING 'UTF8' TEMPLATE template0;
-- REVOKE ALL ON DATABASE investigators_test_db FROM PUBLIC;
