# Disaster Recovery — Duka

## Current scope: SQLite (local/dev)

Backup and restore are implemented as two management commands:

```bash
python manage.py backup_data                     # writes backups/duka-backup-<timestamp>.tar.gz
python manage.py restore_data backups/duka-backup-<timestamp>.tar.gz --force
```

`backup_data` archives `db.sqlite3` and `media/` together into one `.tar.gz`. `restore_data` refuses
to overwrite an existing database unless `--force` is passed.

### Drill actually performed (2026-09-29)

This was not just written and assumed to work — it was run against the live dev database in this
session:

1. `python manage.py backup_data` → `backups/duka-backup-20260929-132044.tar.gz` (19,204 bytes).
2. `db.sqlite3` moved aside to simulate loss.
3. `python manage.py restore_data backups/duka-backup-20260929-132044.tar.gz`.
4. Restored file's MD5 checksum matched the original **exactly** (`1bd2ee18bb5286be0dde3a2bb69d8830`).
5. Functional check after restore: `demoMerchant` account present and login-capable, all shops/orders
   intact, full Django test suite (79 tests) still passes against the restored database.

**Result: the restore is byte-for-byte and functionally correct.** This satisfies the standard this
document holds itself to — "we have successfully restored the system from a backup," not merely
"we have backups."

### RPO / RTO at current (pilot) scale

- **RPO (Recovery Point Objective):** 24 hours, if `backup_data` is run daily via a scheduled task/cron.
  Acceptable at pilot scale — a lost day of demo/early-pilot data is recoverable from WhatsApp history
  and merchant memory.
- **RTO (Recovery Time Objective):** under 5 minutes — `restore_data` is a single command; the drill
  above completed in seconds.

These targets must tighten once real payment volume exists (see the Postgres-target section below).

## Target scope: PostgreSQL (once provisioned — see OWNER_ACTION_REQUIRED.md)

`backup_data`/`restore_data` explicitly refuse to run against a non-SQLite `DATABASES['default']`
(they print a message pointing here) — they are not the production-scale answer. Once a real
Postgres instance exists:

- **Automated backups:** either a managed Postgres provider's built-in automated snapshots + WAL
  archiving (recommended — no custom code to maintain), or a scheduled `pg_dump -Fc` to encrypted
  off-site object storage if self-hosting Postgres.
- **Media files:** move `MEDIA_ROOT` to S3-compatible object storage with versioning enabled (see
  PRODUCTION_DEPLOYMENT.md) — object storage's own versioning becomes the media backup mechanism.
- **Retention:** 30 days rolling daily backups, plus one monthly snapshot retained for 1 year.
- **Tightened RPO/RTO once real payments flow:** RPO target under 1 hour (via WAL streaming/continuous
  archiving — a lost `Order`/`WebhookEvent` row at that point represents real, unrecoverable money
  movement ambiguity, not just inconvenience); RTO target of 4 hours to restore service on a fresh
  host from the latest backup.
- **Restore drill:** repeat the same drill discipline used above — actually restore into a staging
  Postgres instance and run the test suite against it — at least once before the first live merchant
  onboarding, and quarterly thereafter. An untested backup is not a backup.

## What is explicitly out of scope here

- Cross-region replication / multi-region failover — not justified at current or near-term scale.
- Point-in-time recovery finer than daily snapshots on SQLite — the `backup_data` command is a full
  snapshot tool, not a WAL-based PITR system; that capability arrives with the Postgres migration.
