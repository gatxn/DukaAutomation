# Final Production Audit — Duka

**Date:** 2026-09-29. This is the closing audit of the production-transformation pass that began
from `PRODUCTION_AUDIT.md`. Every claim below is grounded in a test that actually ran or a check
actually executed in this session — not aspirational.

## 1. Executive Summary

Duka went from a well-built but single-tenant, SQLite-only, unauthenticated-throttling prototype
to a system with real RBAC, an audit trail, a correct order/payment/fulfillment state machine with
automatic stock-reservation recovery, login throttling, password reset, PostgreSQL readiness,
structured observability, a working (and drilled) backup/restore process, a systematic cross-tenant
security sweep, adversarial upload-security tests, a CI pipeline, and a real E2E smoke test. The
automated test suite grew from 21 tests to 82, all passing, across 11 phases. No real Postgres,
hosting, domain, or provider account exists in this environment — those remain genuine
owner-blocked items, not silently assumed to be fine.

## 2. Architecture

Kept as a Django monolith (AD-1 in `ARCHITECTURE_DECISIONS.md`) — no microservices split, justified
by actual codebase size (~1,700 LOC after this transformation) and the absence of any evidence a
monolith can't handle. Database-backed job queue kept, extended with Postgres-safe
`SELECT ... FOR UPDATE SKIP LOCKED` claiming (AD-2) rather than introducing Celery/Redis.

## 3. Database

PostgreSQL-ready: `DATABASE_URL` env var switches engines with zero code change required
(`dj-database-url`); SQLite remains the local-dev default. No real Postgres instance exists in this
environment (owner-blocked — see `OWNER_ACTION_REQUIRED.md` item 2), so the switch itself is
untested against a live Postgres server; the underlying Django ORM features used
(`JSONField`, `F()`, `UniqueConstraint`, `select_for_update`) are all Postgres-native and were
already working correctly on the more-constrained SQLite backend.

## 4. Authentication

Login throttling added (`django-axes`, 5-attempt limit, 1-hour cooloff — empirically verified
locking out after the 5th failure and staying locked even against the correct password). Full
password-reset flow added, which required also adding optional email capture at signup (previously
missing entirely — would have made reset match zero accounts) and a self-service account-email
endpoint for pre-existing accounts.

## 5. Authorization / RBAC

`Membership` model: Owner / Manager / Agent, one shop per user, backward-compatible with the
existing `Shop.owner` field (self-healing fallback, zero manual data migration risk). Every
previously owner-only endpoint now goes through `shop_for_user(user, min_role=...)`. Staff
invite/role-change/remove are owner-only, cannot demote/remove the last owner. Adversarially
tested: neither Agent nor Manager can self-escalate via any staff endpoint, in any of 4 dedicated
tests added in the final review pass.

## 6. Security

Login throttling, password reset, an audit log (`AuditLog`, wired into login/logout/staff/
settings/product/job actions), a systematic cross-tenant sweep (every tenant-scoped endpoint tested
against a second shop, not spot-checked), and an adversarial upload-security suite (path traversal,
SVG/XXE, decompression bombs, zero-byte files — all confirmed rejected or neutralized by the
existing "fully decode, strip EXIF, re-encode with a random name" pipeline). A machine-readable
error `code` was added to every JSON error response via one middleware, without touching ~30
existing call sites individually.

## 7. Multi-tenancy

Unchanged core guarantee (`shop__owner=request.user`/`shop=shop` scoping) plus the new systematic
sweep proving it holds across every endpoint added or modified this session, not just the
pre-existing ones.

## 8. Payments

`Order.payment_status`/`fulfillment_status` split from the old single `status` field, with explicit
transition methods (`mark_paid`, `mark_expired`, `cancel`, `mark_ready_for_delivery`,
`mark_delivered`) that reject illegal transitions — verified with dedicated state-machine tests
for every legal and illegal transition. Stock-reservation expiry now runs automatically (30-minute
window, matching the existing quote-expiry window) via the worker's heartbeat loop, releasing stock
exactly once (verified: a second sweep does not double-release). A `reconcile_payments` command
cross-checks pending orders against locally-received Snippe webhook events; live reconciliation
against Snippe's own API requires real credentials (owner-blocked).

## 9. Messaging

Unchanged from the prototype's already-strong foundation (HMAC verification, freshness window,
deduplication) — this session added a dedicated end-to-end test for the takeover/manual-reply flow
(FR-18), which previously had no automated coverage: verified it pauses the assistant, clears the
pending quote, queues correctly, and actually sends through a mocked provider.

## 10. AI

Unchanged guardrails (server-side price/stock recalculation, exact-keyword confirmation, `store:
false`) — already strong per the original audit and not touched this session beyond the
takeover-flow test above.

## 11. Background Jobs

Postgres-safe multi-worker claiming added (`SKIP LOCKED`), with the original single-worker-safe
SQLite path kept unchanged and still the default. Stock-reservation expiry added to the existing
heartbeat/stuck-job-sweep loop.

## 12. Frontend

Staff management UI (owner-only), role-appropriate restricted views for Agents on Settings and
Assistant screens (previously would have shown fully-editable forms an Agent couldn't actually
submit), self-service account-email UI, and fulfillment action buttons (ready/delivered/cancel) on
the order detail dialog — all verified working live in the browser, not just unit-tested.

## 13. Responsive Design

All 8 QA-matrix viewport sizes (1920×1080 down to 360×800) checked across all 6 views — zero
horizontal overflow found anywhere, including the new photo-management dialog and Staff section at
390px.

## 14. Accessibility

One real gap found and fixed: the staff "Remove" button had no accessible name distinguishing
which staff member it removes. Confirmed existing global `:focus-visible` styling automatically
covers all new interactive elements (standard `<button>`/`<select>`/`<input>` usage throughout).

## 15. Performance

Not a focus this pass — no evidence of a performance problem exists (no real traffic data), and the
plan explicitly avoided speculative optimization without a measured bottleneck.

## 16. Testing

Suite grew from 21 to 82 tests, all passing: RBAC/audit (15), order state machine (4) + stock
reservation (4) + order actions (2) + reconciliation (1), security/login-throttling/password-reset
(7), cross-tenant sweep (3), upload hardening (6), takeover flow (4), observability (7), job-claim
(4), backup/restore (2), plus the original 21. A real Playwright E2E smoke test additionally passed
against the live running app.

## 17. CI/CD

`.github/workflows/ci.yml` written (lint, migration-check, test, deploy-check) and every step
verified to pass locally exactly as configured. Not yet connected to git/GitHub — deferred per your
explicit instruction this session; see `OWNER_ACTION_REQUIRED.md` item 10.

## 18. Deployment

`PRODUCTION_DEPLOYMENT.md` written, 20 steps, referencing only what's actually been built. No real
deployment has occurred (no hosting/domain exist in this environment).

## 19. Monitoring

Structured JSON logging, `/healthz/` (verified live, checks database + worker heartbeat staleness,
requires no auth), and an optional Sentry hook (no-op until `SENTRY_DSN` is set) — all added and
verified working.

## 20. Backups / DR

A real backup→restore drill was run against the live dev database this session: byte-identical MD5
match, full functional verification (login-capable account, correct data counts, full test suite
passing against the restored database). See `DISASTER_RECOVERY.md`.

## 21. Compliance / Data Governance

Not addressed this pass — correctly deferred as an owner/legal decision (Tanzania-specific data
protection, retention policy), not an engineering task. See `OWNER_ACTION_REQUIRED.md` item 9.

## 22. Remaining External Dependencies

Real Ghala account, real Snippe account, real OpenAI key, hosting, a domain, an SMTP provider, and
optionally Sentry — all detailed with exact steps in `OWNER_ACTION_REQUIRED.md`.

## 23. Remaining Owner Decisions

Merchant segment, delivery-fee model, subscription/billing model, refund policy, data retention
period, RBAC granularity confirmation, AI usage limits, incident-response ownership, compliance
review scope, and pilot-to-launch gating criteria — see `OWNER_ACTION_REQUIRED.md` item 9 and the
Master Specification's Decisions Required section for full detail.

## 24. Known Limitations

- Staff invitation uses an owner-set temporary password, not an email-link flow (AD-4) — a
  reasonable interim design, not a bug.
- No dedicated Orders-page filter tab for the new Expired/Cancelled states (they still appear under
  "All orders" with a distinct pill color) — cosmetic, not a functional gap.
- No returns/refunds workflow, billing/subscription system, or operator console beyond raw Django
  admin — all correctly deferred as owner-decision-gated (Prototype Audit's original
  "Commercial launch" phase items), not silently skipped.
- Real Postgres, real provider accounts, and real deployment remain untested by necessity (no such
  infrastructure exists in this environment) — every piece of code that depends on them is written,
  tested against equivalent local conditions where possible, and clearly flagged rather than
  assumed to work.

## 25. Final Readiness Status

**READY FOR CONTROLLED PILOT.**

Not "PRODUCTION READY" outright: that status requires the items in `OWNER_ACTION_REQUIRED.md`
(real provider accounts, hosting, a real Postgres cutover with its own drill) which are
infrastructure/business steps outside what engineering can complete in this environment. Every
technical item that engineering could complete without those dependencies has been completed,
tested, and verified — not merely implemented and assumed to work.
