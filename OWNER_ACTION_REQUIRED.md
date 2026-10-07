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
**After you have it:** Follow `PRODUCTION_DEPLOYMENT.md`, or `RENDER_DEPLOY.md` if deploying to
Render specifically (a ready-to-use `render.yaml` blueprint is committed at the repo root — note
Netlify does not work for this app: it has no way to run a persistent WSGI process or the
continuously-running background worker this app needs, and no persistent database storage).

## 2. A managed PostgreSQL database and object storage (Supabase)

**What:** A Supabase project (free tier is enough to start) — its Postgres database for
`DATABASE_URL`, and its S3-compatible Storage bucket for product photos, so both live on the same
account. You create the project, bucket, and keys; nothing here can be done on your behalf since it
requires a Supabase account and its dashboard.
**Why:** SQLite is not safe for concurrent production writes (Prototype Audit AR5), and Render's own
disk does not reliably persist uploaded photos (confirmed directly against a live deploy) — a
Postgres instance and object-storage bucket are both required, and Supabase's free tier covers both
without paying for Render's disk add-on. The application code is already wired for both (Phase 6 of
this transformation, plus the `django-storages` integration) — this is purely an infrastructure/
account step.
**Where:** supabase.com — full walkthrough (creating the project, connection string, bucket, and
S3 keys) is in `RENDER_DEPLOY.md` → "Setting up Supabase".
**Environment:** Staging and production.
**Env vars:** `DATABASE_URL` (Supabase's Transaction/pooled connection string), plus
`SUPABASE_S3_ENDPOINT`, `SUPABASE_S3_ACCESS_KEY_ID`, `SUPABASE_S3_SECRET_ACCESS_KEY`,
`SUPABASE_S3_BUCKET`, `SUPABASE_PUBLIC_URL`.
**After you have it:** Set all six env vars in your hosting provider's dashboard (never in a
committed file), run `python manage.py migrate`, run the test suite against it once to confirm,
upload a test product photo and confirm it's still reachable after a redeploy, then run one real
backup→restore drill (see `DISASTER_RECOVERY.md`) before trusting it with real data.

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

## 7. A Resend account (email OTP for signup + password reset)

**What:** A Resend account (resend.com) with a verified sending domain, and an API key.
**Why:** Signup and password reset both send a one-time code by email as one of the two
verification channels (the other is WhatsApp, item 7b below). Without `RESEND_API_KEY` set,
choosing email fails immediately with a clean "Email delivery is not configured yet." error —
by design, not a bug — rather than silently pretending to send.
**Where:** resend.com. You'll need to verify a domain you control before `RESEND_FROM_EMAIL`
(e.g. `Duka <no-reply@yourdomain.com>`) can send to arbitrary recipients — Resend's sandbox
sender only delivers to your own verified test addresses.
**Environment:** Staging/production.
**Env vars:** `RESEND_API_KEY`, `RESEND_FROM_EMAIL`.

## 7a. (Cheapest option) Meta WhatsApp Cloud API directly, for WhatsApp OTP

**What:** Skip the middleman: register a number directly with Meta and send OTPs through the
Cloud API, paying only Meta's per-message fee (about $0.004 per OTP in Tanzania) with no
provider markup. The code already supports it (`shop/providers.py: _cloud_whatsapp_otp`) and
prefers it over Ghala whenever `WHATSAPP_CLOUD_TOKEN` is set.
**Steps (all yours, in Meta's dashboards):**
1. developers.facebook.com → create an app (type "Business"), add the **WhatsApp** product.
2. In WhatsApp Manager, add a **fresh** phone number (not active on any WhatsApp account) and
   verify it by SMS/voice. Copy its **Phone number ID**.
3. Create an **Authentication** message template with a copy-code button, and wait for approval.
4. Business Settings → Users → **System users** → create one, assign the WhatsApp account, and
   generate a **permanent token** with the `whatsapp_business_messaging` permission.
**Env vars (set in Render yourself, never in chat):** `WHATSAPP_CLOUD_TOKEN`,
`WHATSAPP_PHONE_NUMBER_ID`, `WHATSAPP_OTP_TEMPLATE_NAME`, and `WHATSAPP_OTP_TEMPLATE_LANGUAGE`
(defaults to `en`; use the exact code the template was approved in, e.g. `en_US`).
**Not yet verified:** the template send format follows Meta's documented authentication-template
structure and is covered by unit tests, but has not been exercised against Meta's live API, since
that needs your credentials. Test with your own number first.

## 7b. A platform-level Ghala team + an approved WhatsApp template (WhatsApp OTP)

**What:** A **separate Ghala team you (the platform operator) own** — not any individual
merchant's shop — with a WhatsApp Business number connected to it, a team API key minted from
Settings → Developer → API Keys, and — this is the slow part — a Meta-approved "Authentication"
message template submitted through that team's Ghala dashboard.
**Why:** This can't reuse a merchant's own Ghala connection (`Connection.ghala_token` in
`shop/models.py`): that credential is created per-shop, after a shop already exists, and a
brand-new signup has no shop yet to hold one — so the platform needs its own account instead,
the same way it has its own Resend account for email OTP. Separately, WhatsApp Business messaging
to someone who hasn't messaged your business first (exactly what an OTP is) requires a
pre-approved template regardless of provider; the code sends via `template_name` for this reason
(`shop/providers.py: whatsapp_otp`), but the template itself has to exist and be approved before
any code you set here does anything. Template approval is entirely outside engineering's control
and can take real time — start it early if WhatsApp OTP matters for launch. Without
`GHALA_API_KEY`/`GHALA_OTP_TEMPLATE_NAME` set, choosing WhatsApp fails immediately with a clean
"WhatsApp delivery is not configured yet." error.
**Where:** v2.ghala.io — sign up, create a team, connect a WhatsApp number (Embedded Signup),
mint an API key, then submit an Authentication template and wait for it to clear Meta review.
**Environment:** Staging/production.
**Env vars:** `GHALA_API_KEY`, `GHALA_OTP_TEMPLATE_NAME` (the approved template's name),
`GHALA_OTP_TEMPLATE_LANGUAGE` (defaults to `en` — match whatever language you submitted the
template in).

## 7c. Africa's Talking account (SMS OTP, no Meta needed)

**What:** A third signup/reset channel by SMS, built so verification does not depend on WhatsApp.
**Why:** Meta restricts new WhatsApp accounts until the business is verified; SMS works today.
**Steps (yours):** create an Africa's Talking account, top up the wallet, and copy your username and
an API key. Start with username `sandbox` to test with their simulator (no real SMS is delivered).
A registered sender ID is optional; without one the provider's shared sender is used.
**Env vars (set in Render yourself, never in chat):** `AFRICASTALKING_USERNAME`,
`AFRICASTALKING_API_KEY`, and optionally `AFRICASTALKING_SENDER_ID`.
**Not yet verified:** unit-tested against mocks only; send one real SMS to your own number first.

## 7d. Inbound WhatsApp and the AI assistant over Meta directly

**What:** Customer messages to your WhatsApp number reach the AI assistant, which replies through
the Cloud API (`POST /webhooks/meta/`). No Ghala account is needed.
**Needs (all yours, after the number is live):**
1. Meta app → Basic settings → copy the **App secret** into Render as `META_APP_SECRET`.
2. Pick any random string as `META_WEBHOOK_VERIFY_TOKEN` in Render, then in Meta → WhatsApp →
   Configuration set the callback URL to `https://dukanibot.com/webhooks/meta/`, enter the same
   token, verify, and subscribe to the **messages** field.
3. In Duka → Settings → WhatsApp Cloud API, enter the **Phone number ID** and permanent token. Duka
   checks them against Meta before saving. In Assistant, add your OpenAI key and enable replies.
**Worker:** `INLINE_JOB_PROCESSING=1` (already set in `render.yaml`) makes the web process run the
queued jobs, because the free plan has no worker. A restart mid-reply leaves a job failed or stuck;
Settings shows it and its Retry button resends. Use a real `process_jobs` worker before real volume.
**Not yet verified:** the webhook shape, signature and send format follow Meta's documentation and
are unit-tested with mocks, but nothing has run against Meta's live API.

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
