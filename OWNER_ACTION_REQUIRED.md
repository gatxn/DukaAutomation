# Owner Action Required

Everything in this document requires you personally — an external account, a credential, a
business decision, or something else engineering cannot do on your behalf. Nothing here is normal
programming work; if it were, it would already be done. For each item: what it is, why it's
needed, where to get it, which environment it affects, the exact environment variable to set, and
what to do once you have it.

## 1. Hosting

**What:** A server or PaaS to run the Django app + worker process (e.g. a VPS, Railway, Render,
Fly.io, or similar).
**Why:** This has only ever run on `localhost` in this session. There is no deployed instance.
**Where:** Any provider that can run a Python/Django app + a background worker process.
**Environment:** Production (and ideally a separate staging instance first).
**Env vars affected:** `DJANGO_ALLOWED_HOSTS`, everything else in `.env.example` ultimately runs
on whatever host you provision.
**After you have it:** Follow `PRODUCTION_DEPLOYMENT.md`.

## 2. A managed PostgreSQL database

**What:** A provisioned PostgreSQL instance (managed, e.g. via your hosting provider, is strongly
recommended over self-hosting Postgres).
**Why:** SQLite is not safe for concurrent production writes (Prototype Audit AR5). The
application code is already Postgres-ready (Phase 6 of this transformation) — this is purely an
infrastructure step.
**Where:** Your hosting provider's managed Postgres offering, or any Postgres host.
**Environment:** Staging and production.
**Env var:** `DATABASE_URL=postgres://user:password@host:5432/dbname`
**After you have it:** Set `DATABASE_URL`, run `python manage.py migrate`, run the test suite
against it once to confirm, then run one real backup→restore drill (see `DISASTER_RECOVERY.md`)
before trusting it with real data.

## 3. A domain name and TLS

**What:** A custom domain (e.g. `shop.yourbusiness.co.tz`) pointed at your hosting provider, with
HTTPS.
**Why:** Ghala and Snippe both need a real public HTTPS URL to deliver webhooks to; `localhost`
cannot receive them. Most hosting providers issue free TLS certificates (e.g. via Let's Encrypt)
automatically once a domain is attached.
**Where:** Any domain registrar + DNS pointed at your host.
**Environment:** Production.
**Env var:** `DJANGO_ALLOWED_HOSTS=yourdomain.com`; the domain itself is entered as "Your app's
HTTPS URL" in the app's own Settings screen once deployed.

## 4. A Ghala WhatsApp Business account and API credentials

**What:** A real Ghala account with API access and a connected WhatsApp Business number.
**Why:** This is the entire messaging integration's real-world endpoint. The adapter, webhook
verification, signature checking, deduplication, and outbound-send logic are all built and unit
tested (Prototype Audit W1–W32) — but no real Ghala event has ever been processed (D2, D8).
**Where:** ghala.io — Developer → Credentials for the API token, Developer → Webhooks after you
register (the app does this automatically once you save your token and public URL).
**Environment:** Staging first, ideally, then production.
**Where it's entered:** The signed-in Settings screen (not an environment variable) — tokens are
encrypted per-shop in the database, by design (never in `.env`).
**After you have it:** Follow the guided setup already in Settings: save the token, save your
public HTTPS URL, register the webhook, then inspect one real `message.received` event in Ghala's
own dashboard and copy its actual field paths into the Incoming Message Field Mapping section
(the app cannot guess this — Ghala's payload shape isn't published, per the Prototype Audit's own
finding).

## 5. A Snippe merchant account and API credentials

**What:** A real Snippe account with API/sessions access for mobile-money checkout.
**Why:** Same situation as Ghala — the checkout creation, signature verification, and payment
confirmation logic are built and tested (S1–S27), but no real payment has ever completed (D10).
**Where:** Snippe's merchant dashboard — Settings → Webhook Secret, and your sessions bearer
token.
**Environment:** Staging first, then production.
**Where it's entered:** The signed-in Settings screen, same as Ghala.
**After you have it:** Save the token and webhook secret, then run one real low-value transaction
end to end before trusting it with real customer payments.

## 6. An OpenAI API key with billing enabled

**What:** A real OpenAI API key.
**Why:** The AI sales assistant calls `/v1/responses` with structured output; without a real key
every call fails with a clear `ProviderError` by design (A8) — there is no mock mode for real use.
**Where:** platform.openai.com.
**Environment:** Staging/production (a key can also be used to manually test the in-app "Try a
conversation" preview, which never creates real orders/messages regardless).
**Where it's entered:** The signed-in Settings → Assistant screen.

## 7. An SMTP provider (for password-reset emails)

**What:** Real SMTP credentials (e.g. from SendGrid, Postmark, AWS SES, or your hosting
provider's built-in mail service).
**Why:** Password reset (added in Phase 4 of this transformation) sends real emails in
production; the console backend used in dev just prints to stdout.
**Where:** Any transactional-email provider.
**Environment:** Production.
**Env vars:** `EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend`, `EMAIL_HOST`,
`EMAIL_PORT`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`, `EMAIL_USE_TLS`, `DEFAULT_FROM_EMAIL`.

## 8. A Sentry (or GlitchTip) account (optional but recommended)

**What:** An error-tracking project.
**Why:** The integration point is already wired (Phase 5) and is a complete no-op until you set
the DSN — nothing breaks either way, but you'll have no visibility into production errors without
it.
**Where:** sentry.io, or a self-hosted GlitchTip instance.
**Environment:** Production (staging optional).
**Env var:** `SENTRY_DSN=https://...`

## 9. Business decisions (not technical — see the Master Specification's Decisions Required section for full detail)

These block specific roadmap phases and cannot be inferred by engineering:

- **Target merchant segment** and delivery-fee/zone model (blocks refining the flat delivery fee).
- **Subscription/billing model** (blocks Phase 18 of the original roadmap — billing subsystem).
- **Refund/return policy** (the state machine supports cancellation; the policy itself is yours).
- **Data retention period** for conversations/orders (compliance-relevant; not an engineering call).
- **RBAC granularity** — confirm the three-tier Owner/Manager/Agent model (built this session) is
  sufficient, or tell us what finer-grained permission a real employee needs.
- **AI usage/cost limits per merchant**, if any, before commercial launch.
- **Incident-response ownership** — who is on call once real customer data flows through the
  system.
- **Legal/compliance review scope** (Tanzania-specific data protection, mobile-money regulation) —
  whether required before a controlled pilot or deferrable to commercial launch.
- **Pilot-to-launch gating criteria** — what specifically must be true before you approve moving
  from controlled pilot to paid/commercial launch.

## 10. Connecting this project to git/GitHub

**What:** `git init`, adding the `https://github.com/gatxn/DukaAutomation.git` remote, and an
initial push.
**Why:** You explicitly asked to skip this for now during this transformation (manual timestamped
backups under `_backups/` were used as the safety net instead). The CI workflow
(`.github/workflows/ci.yml`) is already written and verified to pass every step locally — it will
do nothing until the repository is actually connected and pushed.
**After you have it:** `git init`, `git remote add origin https://github.com/gatxn/DukaAutomation.git`,
commit, push. The CI pipeline runs automatically from that point on.
