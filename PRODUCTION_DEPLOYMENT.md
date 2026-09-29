# Production Deployment Guide — Duka

This assumes you've provisioned hosting and PostgreSQL per `OWNER_ACTION_REQUIRED.md`. Architecture
decision: Gunicorn + Nginx + PostgreSQL, no containers required (though a `Dockerfile` is a
reasonable addition later for reproducibility — not required by the "keep it simple" decision
already made for this project).

## 1. Provision infrastructure

A host that can run: a Python 3.13 WSGI process (Gunicorn), a reverse proxy (Nginx), and a
continuously-running worker process (`manage.py process_jobs`). A managed PostgreSQL instance
(see item 2 in `OWNER_ACTION_REQUIRED.md`).

## 2. Configure environment

Copy `.env.example` to your host's real environment variable configuration (never commit a real
`.env` file — `.gitignore` already excludes it). At minimum: `DJANGO_DEBUG=0`, `DJANGO_SECRET_KEY`
(generate a real one — `python -c "import secrets; print(secrets.token_urlsafe(50))"`),
`DJANGO_ALLOWED_HOSTS`, `DATABASE_URL`, `CREDENTIAL_ENCRYPTION_KEY` (generate once, back it up
securely — losing it makes every merchant's saved credentials unreadable).

## 3. Configure PostgreSQL

Set `DATABASE_URL` (see `.env.example`). The app auto-switches engines based on this — no code
change needed (Phase 6 of this transformation made this Postgres-ready).

## 4. Configure media storage

`MEDIA_ROOT` defaults to local disk, which does not survive a redeploy to a fresh
filesystem/container. For any host that can be replaced/recreated, configure object storage (S3 or
S3-compatible) instead — this requires `django-storages` (not currently a dependency; add it when
you reach this step, since it's meaningless without a real object storage account to point it at).

## 5. Configure secrets

Via your host's environment variable mechanism (never in source). `CREDENTIAL_ENCRYPTION_KEY` and
`DJANGO_SECRET_KEY` are the two that must never be lost or leaked — the app raises a hard error at
startup if `CREDENTIAL_ENCRYPTION_KEY` is unset while `DJANGO_DEBUG=0`.

## 6. Run migrations

```bash
python manage.py migrate
```

All migrations in this repo (including the RBAC/audit-log/order-state-machine ones added in this
transformation) are plain schema + data migrations — no manual SQL required.

## 7. Create admin/operator accounts

The first merchant account is created via `/signup/` like any other — there is no separate "platform
admin" concept yet (the Prototype Audit flagged this as a Commercial-launch-phase gap, ADM2). For
Django's own `/admin/`, create a superuser:

```bash
python manage.py createsuperuser
```

## 8. Configure provider credentials

Ghala/Snippe/OpenAI credentials are entered per-shop through the signed-in Settings screen, not
environment variables (see `OWNER_ACTION_REQUIRED.md` items 4–6).

## 9. Configure domain

Point your domain's DNS at your host. Set `DJANGO_ALLOWED_HOSTS` to match.

## 10. Configure TLS

Use your host/provider's TLS mechanism (most managed platforms and Nginx + Let's Encrypt handle
this automatically). Once `DJANGO_DEBUG=0`, the app already forces
`SECURE_SSL_REDIRECT`/`SESSION_COOKIE_SECURE`/`CSRF_COOKIE_SECURE`/HSTS — verified by
`manage.py check --deploy` passing clean in this session.

## 11. Start application

```bash
gunicorn duka.wsgi:application --bind 0.0.0.0:8000 --workers 3
```
(behind Nginx as a reverse proxy, serving `/static/` directly via `collectstatic` output)

```bash
python manage.py collectstatic --noinput
```

## 12. Start worker

```bash
python manage.py process_jobs
```

Safe to run as a single worker (the documented baseline) or multiple parallel workers once
PostgreSQL is in use (Phase 6 added `SELECT ... FOR UPDATE SKIP LOCKED` job claiming for this).
Run under a process supervisor (systemd, or your platform's process manager) so it restarts on
crash.

## 13. Run health checks

```bash
curl https://yourdomain.com/healthz/
```

Should return `{"status": "ok", ...}`. This endpoint requires no authentication by design (for
load balancers/uptime monitors).

## 14. Run smoke tests

```bash
python manage.py test
```

77+ tests should pass (79 as of this transformation, plus the Playwright E2E smoke test in `e2e/`
if Node is available on your CI/staging host — see `e2e/README` usage below).

## 15. Verify payment integration

Once Snippe credentials are saved (item 5 in `OWNER_ACTION_REQUIRED.md`), run one real low-value
transaction through the live flow before trusting it with real customer money.

## 16. Verify messaging integration

Once Ghala credentials are saved and the webhook is registered, send one real WhatsApp message to
the connected number and confirm it appears in the Inbox — this is also when you'll discover
Ghala's actual `message.received` field shape to fill in the Incoming Message Field Mapping.

## 17. Verify monitoring

Confirm `/healthz/` is being polled by an uptime monitor, and that `SENTRY_DSN` (if set) is
receiving events — trigger a deliberate test error to confirm.

## 18. Verify backups

Run `python manage.py backup_data` (SQLite) or your chosen Postgres backup mechanism (see
`DISASTER_RECOVERY.md`) and confirm the archive/snapshot is retrievable.

## 19. Verify restore

Actually restore from that backup into a staging copy and confirm the app still works — an
untested backup is not a backup. This transformation's own SQLite drill (see
`DISASTER_RECOVERY.md`) is the template to follow.

## 20. Perform production release

Once 1–19 are done and verified, cut over DNS/traffic. Keep the previous environment running
briefly in case of rollback.

## Rollback

Since this deployment has no containers/image versioning by default: keep the previous release's
code checked out in a separate directory (or use your platform's built-in release history if it
has one), and keep the pre-migration database backup from step 18 so a schema rollback is possible
via `restore_data` if a migration needs to be undone.
