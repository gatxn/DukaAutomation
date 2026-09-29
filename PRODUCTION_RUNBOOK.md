# Production Runbook — Duka

## Application outage

1. Check `/healthz/` — if it returns 503 or times out, the app process or DB is down.
2. Check the WSGI process (Gunicorn) is running under your process supervisor; restart if not.
3. Check `/healthz/`'s `checks.database` field — `"error"` means the DB connection itself is the
   problem (see Database outage below), not the app.

## Database outage

1. `/healthz/` will report `"database": "error"`.
2. Check your PostgreSQL provider's status page/dashboard first (managed Postgres outages are
   usually provider-side).
3. If self-hosted, check the Postgres process/host directly.
4. Once restored, `/healthz/` should self-recover on the next poll — no app restart needed.

## Worker failure

1. `/healthz/` reports `"worker": "no_active_worker"` if no `Connection.worker_heartbeat` has
   updated in the last 2 minutes across any shop.
2. Check the `process_jobs` process is running under your process supervisor; restart if not.
3. Jobs stuck in `status='running'` for over 5 minutes are automatically requeued by the worker's
   own startup/heartbeat loop — no manual intervention needed once the worker is back up.
4. Check Settings → Connection activity for any jobs stuck in `failed` status; use the Retry
   button (owner/manager only) after fixing the underlying cause shown in the error message.

## Payment failure

1. Check the specific order in Orders — `payment_status` will show `pending`/`failed`/`expired`.
2. Run `python manage.py reconcile_payments` to cross-check pending orders against any Snippe
   webhook events already received for them — a mismatch (event received but order still pending)
   needs manual investigation, since it means `verify_payment()`'s server-side re-check with
   Snippe itself failed or disagreed with the webhook payload.
3. Snippe's own dashboard is the source of truth for whether a payment actually succeeded — never
   trust a customer's claim or screenshot (this is enforced in code, not just policy).

## Webhook failure

1. Failed `kind='webhook'` jobs appear in Settings → Connection activity with their error message
   (sanitized — never raw payloads/secrets).
2. Common causes: signature mismatch (check the webhook secret matches what Ghala/Snippe issued),
   stale field mapping (Ghala changed their event shape — re-inspect a real event and update
   Settings), or the public URL no longer resolving.
3. Retry after fixing the root cause.

## Ghala failure

1. Check Ghala's own status/dashboard first.
2. Check the webhook is still registered (Settings shows "Webhook registered" once done — it does
   not re-verify this live, only tracks that registration succeeded once).
3. Check `ghala_auto_reply_disabled` is still checked in Settings if native Ghala auto-replies
   have been re-enabled on their side (would cause duplicate replies to customers).

## Snippe failure

1. Check Snippe's own dashboard/status first.
2. A `ProviderError` from `checkout()` or `verify_payment()` is surfaced directly in the job's
   error message — read it before assuming a code bug.

## AI provider (OpenAI) failure

1. `ProviderError` from `sales_reply()` surfaces in the `agent`-kind job's error field.
2. Common cause: API key removed/expired, or billing issue on the OpenAI account — check
   platform.openai.com directly.
3. While the assistant is down, conversations simply don't get an AI reply; a merchant can always
   take over manually via the Inbox regardless of assistant state.

## Backup restoration

Follow `DISASTER_RECOVERY.md`. For SQLite: `python manage.py restore_data <archive> --force`. For
PostgreSQL: restore via your provider's snapshot mechanism or `pg_restore`.

## Security incident

1. Rotate the affected credential immediately: `CREDENTIAL_ENCRYPTION_KEY` (all merchant
   credentials become unreadable and must be re-entered — communicate this to merchants first),
   `DJANGO_SECRET_KEY` (invalidates all active sessions), or a specific merchant's
   Ghala/Snippe/OpenAI token (via their own Settings screen).
2. Check `AuditLog` entries for the affected shop/user around the incident window.
3. If a staff account is compromised: remove their `Membership` immediately (Settings → Staff,
   owner-only) — this revokes access without touching their platform-wide Django account.

## Compromised credential

Rotate it. Provider credentials: re-enter in the shop's Settings screen (old value is
overwritten). `CREDENTIAL_ENCRYPTION_KEY`/`DJANGO_SECRET_KEY`: see Security incident above.

## Stuck order

1. Check `payment_status`/`fulfillment_status` in the order detail dialog.
2. A pending order past its 30-minute reservation window is auto-expired by the worker's
   `expire_reservations()` sweep (runs every ~30s alongside the heartbeat) — stock is released
   automatically. No manual action needed unless the worker itself is down (see Worker failure).
3. To manually cancel: use the order detail dialog's "Cancel order" action (manager+, refuses once
   delivered).

## Stuck payment

See Payment failure above; `reconcile_payments` is the diagnostic tool.

## Stuck job

Jobs stuck `running` for 5+ minutes auto-requeue on the worker's next heartbeat. Jobs `failed`
show their error and a manual Retry button once the underlying issue is fixed.

## Failed deployment

Since there's no container-image rollback by default: redeploy the previous release's code from
your platform's release history (or your own kept-around checkout), and restore the
pre-migration database backup if a migration needs to be undone (see `DISASTER_RECOVERY.md`).
