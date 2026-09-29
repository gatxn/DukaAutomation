# Architecture Decisions — Duka Production Transformation

Significant decisions made during this transformation. Format: problem, options considered,
chosen solution, why, trade-offs, consequences.

## AD-1: Keep the Django monolith; no microservices

**Problem:** Should the production redesign split Duka into separate services?
**Options:** (a) stay a single Django app, (b) split into API/worker/webhook microservices.
**Chosen:** (a). **Why:** ~1,200 LOC total; the entire audit found only one architectural
weakness (SQLite), not a monolith-scaling problem. Microservices would add operational complexity
(service discovery, network calls where function calls suffice, distributed tracing) with no
corresponding benefit at this scale. **Trade-offs:** none significant at current scale; revisit
only with concrete evidence of a bottleneck a monolith can't solve. **Consequences:** all Phase
2–9 work (RBAC, state machine, observability) was addable as in-process modules, no network
boundary to design around.

## AD-2: Keep the database-backed job queue; add SKIP LOCKED, not Celery/Redis

**Problem:** The existing `Job` table + `process_jobs` polling loop needs multi-worker safety once
Postgres is in use.
**Options:** (a) keep the DB-backed queue, add `SELECT ... FOR UPDATE SKIP LOCKED` for Postgres,
(b) introduce Celery + Redis.
**Chosen:** (a). **Why:** the existing queue already has idempotency, retry, dead-job detection,
and heartbeat monitoring built and tested; SKIP LOCKED solves the one real gap (multiple workers
converging on the same row) without a new infrastructure dependency (Redis) or a new mental model
(Celery's task routing/serialization). **Trade-offs:** less mature tooling/ecosystem than Celery
(no built-in flower dashboard, etc.) — acceptable given current job volume. **Consequences:**
`process_jobs.py`'s `claim_job()` branches on `connection.vendor`; SQLite dev keeps the original
conditional-update claim unchanged.

## AD-3: RBAC via a `Membership` model, not a Django Groups/Permissions rewrite

**Problem:** Need Owner/Manager/Agent roles per shop; Django ships `Group`/`Permission` but they're
global, not per-tenant.
**Options:** (a) a lightweight `Membership(shop, user, role)` model with hand-rolled role-level
checks, (b) Django's `Group`/`Permission` + `django-guardian` for object-level permissions.
**Chosen:** (a). **Why:** three roles, one dimension (level), no per-object permission matrix
needed — `django-guardian`'s generality would be unused complexity. A `Membership` row per user
(enforced `OneToOneField`) also cleanly generalizes the existing `Shop.owner` field with zero
disruption to the existing `shop__owner=request.user` tenant-scoping pattern (kept as a backward-
compatible synonym, not replaced). **Trade-offs:** a user belongs to exactly one shop — no
multi-shop-per-user support. Acceptable: matches the existing one-shop-per-account product model;
extending to multi-shop membership later is an additive migration (`OneToOneField` → `ForeignKey`),
not a rewrite. **Consequences:** every view's shop lookup now goes through `shop_for_user(user,
min_role=...)`, which self-heals a missing `Membership` from the legacy `Shop.owner` field —
existing tests and data needed zero manual migration.

## AD-4: Staff invitation creates a new Django User directly, not an email-link invite flow

**Problem:** "Staff invitations" (prompt Section 7) implies an email-based invite link; no SMTP
integration point existed before this transformation (added in Phase 4, but for password reset).
**Options:** (a) owner sets a temporary password when adding staff (immediate, no email
dependency), (b) build a token-based email invite link now.
**Chosen:** (a). **Why:** keeps Phase 2 self-contained and immediately usable without also
building/testing an email-token flow in the same pass; the owner already has an SMTP-dependent
Phase 4 (password reset) to configure at launch anyway. **Trade-offs:** the owner must communicate
the temporary password to the new staff member out-of-band (documented in the UI copy).
**Consequences:** documented as a known limitation; extending to a real email-based invite is a
natural follow-up once SMTP is confirmed working, not a redesign.

## AD-5: Keep `Order.status` as a synced display field; `payment_status`/`fulfillment_status` are the real state machine

**Problem:** Splitting one conflated `status` field into two ("payment state" vs "fulfillment
state") risks breaking every existing frontend/API consumer of the old field.
**Options:** (a) replace `status` entirely, updating every read site, (b) add the two new fields
as the authoritative state machine, keep `status` as a derived display string kept in sync by the
new transition methods.
**Chosen:** (b). **Why:** zero frontend/API breakage — `static/app.js`'s `status()` render
function and the Orders filter tabs needed no changes for existing states; new states (Expired,
Cancelled) were added additively. **Trade-offs:** two sources of "current state" exist in the row
(intentional redundancy) — mitigated by only ever writing `status` from inside the transition
methods, never directly. **Consequences:** `Order.mark_paid()`/`mark_expired()`/`cancel()`/
`mark_ready_for_delivery()`/`mark_delivered()` are the only legal way to change order state; direct
`.status = ...` assignment outside these methods would desync the two representations and should
be treated as a bug if ever introduced.

## AD-6: Error-envelope standardization via middleware, not touching every view

**Problem:** ~30+ existing `JsonResponse({'error': ...})` call sites; adding a machine-readable
`code` field to every one individually is high-risk, high-effort, low-value churn.
**Options:** (a) rewrite every error response site, (b) a middleware that adds a default `code`
(derived from HTTP status) to any JSON error body that doesn't already specify one.
**Chosen:** (b). **Why:** purely additive — no existing `error`/`fields` contract changes, no
view-level code touched, works uniformly across the whole app including future new endpoints.
**Trade-offs:** the default codes are coarse (one per HTTP status, e.g. every 400 gets
`invalid_request`) rather than fine-grained business codes. Acceptable: a view can still set a more
specific `code` explicitly if ever needed, and the middleware only fills the gap when one is
missing. **Consequences:** `shop/middleware.py: ErrorEnvelopeMiddleware`.

## AD-7: Structured logging via a small custom formatter, not a new dependency

**Problem:** Need JSON-structured logs for production log aggregation.
**Options:** (a) `python-json-logger` (a small extra dependency), (b) a ~15-line custom
`logging.Formatter` subclass.
**Chosen:** (b). **Why:** the requirement is genuinely simple (one JSON object per log line);
adding a dependency for something this small doesn't pay for itself, consistent with the "keep it
simple" principle applied throughout this transformation. **Consequences:** `duka/logging.py:
JsonFormatter`.
