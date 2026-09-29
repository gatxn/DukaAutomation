# Duka — Pre-Implementation Production Audit

**Date:** 2026-09-29
**Scope:** Full source inspection of `duka-python/duka-python` (Django 5.2, SQLite dev, `runserver`-only). This audit reuses and re-categorizes the evidence-cited findings from the earlier 300+ item "Duka Prototype Audit" (see the Duka Complete Master Specification doc for the full item-by-item checklist) — it is not a re-derivation from scratch. Every claim below is grounded in a specific file/line or test already verified in that audit.

This document drives the implementation order for the production transformation now underway. Severity: **CRITICAL** (blocks any real usage safely) / **HIGH** (blocks a controlled pilot) / **MEDIUM** (blocks paid/commercial launch) / **LOW** (polish, defer).

---

## A. What currently works

- Multi-tenant data isolation via `shop__owner=request.user` / `shop=job.shop` scoping on every tenant model (Shop, Product, Conversation, Order, Connection, WebhookEvent, Job). Cross-shop access returns 404, tested (`test_account_isolation_and_anonymous_access`).
- AI order-authority boundary: the model can only *propose* (product/quantity/address); the server independently recalculates price/stock and requires an exact `CONFIRM`/`THIBITISHA` before any Order is created (`shop/workflow.py`).
- Atomic, race-safe stock reservation: conditional `UPDATE ... WHERE stock >= qty` via `F()`, tested under a repeat-confirmation scenario.
- Webhook security: HMAC-SHA256 signature verification, 5-minute freshness window, `(shop, provider, delivery_id)` uniqueness for deduplication — tested extensively.
- Payment confirmation is server-verified only: `Order.status` is set to Paid exclusively inside `verify_payment()` after re-checking the session with Snippe; no customer/browser-supplied status is ever trusted.
- Credential encryption at rest (Fernet) with write-only API exposure — verified no raw secret is ever returned in a response body.
- Demo/live separation via a server-set `is_demo` boolean, never client-supplied.
- Idempotency by construction: `Job.key`, `Order.payment_key`, `Order.origin_key`, `Message.external_id`, `WebhookEvent` uniqueness.
- 21/21 automated tests pass; `manage.py check` is clean.

## B. What partially works

- Settings UI shows integration status (`configured: true/false`) but nothing confirms Ghala/Snippe are actually *verified* connections until the first real event/payment.
- Empty/loading/error UI states exist (`busy()`, `toast()`, `showError()`, `empty()` helpers) but haven't been exhaustively verified per individual action.
- Accessibility basics exist (`aria-live`, skip-link, `<label for>`) but no screen-reader test has been run.
- `worker_heartbeat` exists as the only observability primitive, surfaced only as a boolean in Settings — not wired to any alerting.

## C. What is broken

- **The dashboard's "Paid sales" figure is misleading.** `static/app.js:23` sums *all* orders regardless of `is_demo`, while its own caption admits "…includes sample orders." This directly misstates revenue to anyone watching a pilot. **CRITICAL** (fixed in Phase 3 of this transformation).
- `Order.status` conflates payment state and fulfillment state in one field — "Ready for delivery" cannot cleanly coexist with "unpaid." **HIGH**.

## D. What is missing

- No staff/role model — `Shop.owner` is a `OneToOneField`, exactly one user per shop. **HIGH** (RBAC, Phase 2).
- No stock-reservation expiry job — an abandoned live order holds stock forever. **HIGH** (Phase 3).
- No audit-log model for admin/business-sensitive actions. **HIGH** (Phase 2).
- No login throttling or password-recovery flow. **HIGH** (Phase 4).
- No returns/refunds/cancellation workflow in any form. **MEDIUM** (documented, deferred past this pass — business policy decision needed first, see `OWNER_ACTION_REQUIRED.md`).
- No billing/subscription system. **MEDIUM** (deferred — pricing model is an owner decision).
- No operator console beyond raw Django admin. **MEDIUM** (deferred).
- No CI pipeline, no staging environment. **HIGH** (Phase 9/owner infra).

## E. Security vulnerabilities

- No brute-force/login-throttling protection on the login view. **HIGH** — Phase 4.
- No password-reset/account-recovery flow. **HIGH** — Phase 4.
- No audit trail for Django admin actions at all — any platform-level change is untraceable. **CRITICAL** — Phase 2.
- AI's free-text `reply` field, once under 4000 chars and URL-free, reaches the customer largely as-is; a subtler injection could still slip through. **MEDIUM** — flagged, mitigations already in place (schema-constrained structured fields) are the primary defense; full adversarial QA tracked as a follow-up requiring real-traffic observation.
- No independently audited proof that *every* query path is tenant-scoped (only the main flows are covered by tests today). **HIGH** — Phase 4 cross-tenant sweep.
- `django.contrib.admin`'s `Connection` model is correctly *not* registered (credentials stay ciphertext even there) — this is a strength, not a gap, and must not regress.

## F. Performance problems

- No caching layer, no CDN/bundling strategy for static assets — acceptable at current scale, flagged for post-pilot (Growth phase per the roadmap), not addressed in this pass since no real traffic data exists yet to profile against.
- No pagination on any list endpoint yet observed to need it at current data volumes — revisit once real merchant catalogs/order histories exist.

## G. Database problems

- SQLite is the only configured engine; not safe for concurrent production writes. **CRITICAL** — Phase 6 (code made Postgres-ready; real migration deferred to when a Postgres instance exists, per user decision this session).
- No migration currently exists proving historical data survives a future schema change (`AR10`/`AR19` — "needs verification," not "broken").

## H. Frontend/UI problems

- No product-edit endpoint exists (create + photo-replace only) — cannot fix a name/price/description typo without the admin. **MEDIUM**, out of this pass's scope (net-new feature, not a production-readiness blocker per se — flagged for follow-up).
- No i18n for dashboard chrome (AI replies respect `shop.language`; dashboard UI does not). **LOW**.

## I. Responsive-design problems

- Not independently re-tested at the full QA viewport matrix (1920×1080 → 360×800) this session prior to this transformation. **Addressed in Phase 7** — live browser verification across the full matrix, fixing only real issues found.

## J. Authentication problems

- No login throttling. **HIGH** — Phase 4.
- No password reset/recovery. **HIGH** — Phase 4.
- No email verification on signup. **MEDIUM** — deferred (needs a real SMTP/email provider, see `OWNER_ACTION_REQUIRED.md`); console/file backend wired for dev in Phase 4.

## K. Authorization/RBAC problems

- No roles of any kind exist — single owner per shop, full authority, no delegation. **HIGH** — Phase 2 (Membership model: Owner/Manager/Agent).
- No staff invitation flow. **HIGH** — Phase 2.

## L. Integration problems

- Ghala inbound-event field mapping has never been verified against a real account (merchant-typed dot-paths, untested against real payload shape). **HIGH but genuinely blocked** — requires a real Ghala account (owner action); the mapping *mechanism* itself is implemented and tested against its own contract.
- No real Snippe payment has ever completed; only mocked-provider tests exist. Same blocker class as above.
- No reconciliation between local Order records and Snippe's actual transactions. **MEDIUM** — Phase 3 builds the local-data-only reconciliation report; live reconciliation against Snippe's API is owner-blocked (needs real credentials).

## M. Deployment problems

- No staging environment. **CRITICAL** — Phase 9/owner infra (this pass prepares CI config and Postgres-ready code; actually standing up staging requires hosting the owner provisions).
- No production WSGI server, reverse proxy, TLS, or domain configured — all owner/infra-provisioning items, documented in `PRODUCTION_DEPLOYMENT.md` and `OWNER_ACTION_REQUIRED.md`.
- No containerization — **LOW**, not required by the "keep it simple" architecture decision already made this session.

## N. Testing gaps

- Product-detail dialog (FR-12) and the takeover flow (FR-18) are manual-only, no automated coverage. **Addressed in Phase 8.**
- No cross-tenant automated sweep across every view (only main flows covered). **Addressed in Phase 4/8.**
- No CI running tests automatically. **Addressed in Phase 9 (config only; connecting git/GitHub is a follow-up the owner controls).**

## O. Observability gaps

- No structured logging, no error tracking, no `/healthz/` endpoint, no alerting. **HIGH** — Phase 5.

## P. Backup/DR gaps

- No backup or restore procedure exists or has ever been exercised. **CRITICAL** — Phase 10 (SQLite-scope backup/restore drill run locally; Postgres-target strategy documented for when a real instance exists).

## Q. Business workflow gaps

- No returns/refunds/cancellation workflow. **MEDIUM** — architecture prepared (state machine in Phase 3 supports a `cancelled` fulfillment state); full workflow UI/policy is owner-decision-gated (refund policy is not an engineering decision) and tracked in `OWNER_ACTION_REQUIRED.md`/the Master Specification's Decisions Required section.
- No billing/subscription system. **MEDIUM** — same treatment; pricing model is an owner decision.

## R. Production blockers

Ranked, matching the roadmap this transformation follows:

1. **CRITICAL** — No audit trail for admin actions (Phase 2).
2. **CRITICAL** — SQLite in the production path (Phase 6 makes code ready; real cutover needs a provisioned Postgres instance — owner infra).
3. **CRITICAL** — No backup/DR process (Phase 10).
4. **HIGH** — No RBAC (Phase 2).
5. **HIGH** — No login throttling/recovery (Phase 4).
6. **HIGH** — No stock-reservation expiry (Phase 3).
7. **HIGH** — No structured logging/error tracking/health check (Phase 5).
8. **HIGH** — No staging environment / CI (Phase 9, owner infra for the rest).
9. **MEDIUM** — No returns/billing/operator console (owner-decision-gated, deferred past this pass).

**Implementation order chosen** (risk- and dependency-driven, not prompt-order): safety net → RBAC/audit foundation (everything else's permission checks depend on this existing first) → order/payment state integrity (core money-safety) → auth/security hardening → observability → database portability → frontend QA → test hardening → CI config → backup/DR + docs → final adversarial review. This matches the phase plan in the active execution plan for this transformation.
