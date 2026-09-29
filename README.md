# Duka — WhatsApp commerce for Tanzania

A Python/Django merchant workspace with a Ghala-inspired interface, product photos, Ghala WhatsApp integration, Snippe hosted checkout and an AI sales assistant. Live integrations are implemented but have not been exercised with real provider credentials. All included customer records and orders are clearly marked sample data.

**Production status:** this project has been through a full production-transformation pass — see [`PRODUCTION_AUDIT.md`](PRODUCTION_AUDIT.md) for the audit, [`FINAL_PRODUCTION_AUDIT.md`](FINAL_PRODUCTION_AUDIT.md) for the outcome, [`PRODUCTION_DEPLOYMENT.md`](PRODUCTION_DEPLOYMENT.md) for how to deploy it, [`PRODUCTION_RUNBOOK.md`](PRODUCTION_RUNBOOK.md) for operating it, [`OWNER_ACTION_REQUIRED.md`](OWNER_ACTION_REQUIRED.md) for what only you can do next, [`ARCHITECTURE_DECISIONS.md`](ARCHITECTURE_DECISIONS.md) for why things are built the way they are, and [`DISASTER_RECOVERY.md`](DISASTER_RECOVERY.md) for backup/restore (a real drill has been run and verified).

## Run locally

Requires Python 3.10+. From this folder:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe manage.py migrate
.\.venv\Scripts\python.exe manage.py runserver 127.0.0.1:8000
```

Open http://127.0.0.1:8000/signup/ and create your shop. In a second terminal, from the same folder:

```powershell
.\.venv\Scripts\python.exe manage.py process_jobs
```

Safe to run as a single worker (the baseline) or multiple parallel workers once `DATABASE_URL` points at PostgreSQL (job claiming uses `SELECT ... FOR UPDATE SKIP LOCKED` there; SQLite keeps the original single-worker-safe claim). It processes signed webhooks, AI responses, outgoing messages, and a stock-reservation-expiry sweep, all from a database-backed queue. Failed jobs are visible in **Settings → Connection activity**; fix the issue and click **Retry**. Jobs are not automatically retried after provider failures. Interrupted jobs become eligible again after five minutes. The worker and web server must share the same database and encryption key.

On macOS/Linux use `.venv/bin/python`. Optional administration: `python manage.py createsuperuser`, then `/admin/`.

## Where to put your live APIs

The app provides password-style, write-only fields. Blank means keep the existing credential. Replacement credentials overwrite the encrypted value. Do not paste real keys into source code, screenshots or chat.

| Screen | Field | What to paste |
| --- | --- | --- |
| Settings → Public address | Your app’s HTTPS URL | Your deployed domain, e.g. `https://shop.example.com` |
| Settings → Ghala | API bearer token | Connected-number token from Ghala Developer → Credentials |
| Settings → Ghala | Webhook secret | The secret returned when registering that webhook |
| Settings → Snippe | Sessions bearer token | Merchant bearer JWT/token authorized for `/api/v1/sessions` |
| Settings → Snippe | Webhook secret | Snippe Settings → Webhook Secret |
| Assistant | OpenAI API key | A key from your OpenAI API project with billing enabled |

“Token saved” indicates local storage, not a verified provider connection. Production credentials are encrypted using `CREDENTIAL_ENCRYPTION_KEY`; debug mode derives a local development key from the local Django secret. Keep the encryption key stable and backed up. On moving from local debug to production, re-enter credentials unless you deliberately preserve the same encryption key.

The [`.env.example`](.env.example) file documents deployment variables only. Django does not automatically load it: configure these values in your host environment. Merchant tokens are entered per shop in the UI.

## Connect WhatsApp through Ghala

1. Use a Ghala account/plan with API access and a connected WhatsApp Business number.
2. Save your public HTTPS domain and Ghala token in Duka Settings.
3. Click **Register webhook with Ghala**, or manually register the displayed callback URL for `message.received` and `message.status`. Automatic registration stores the returned signing secret. If registered manually, paste that secret into Duka.
4. Open Ghala **Developer → Events** and inspect an actual `message.received` event for your account.
5. In Duka’s **Incoming message field mapping**, enter dot-separated paths for the phone, text, message ID and timestamp. Customer name is optional. Check the verification box only after matching a real event.
6. Disable Ghala native AI auto-replies and confirm this in Duka so two assistants do not answer the same customer.
7. Configure the assistant, run the worker and send an inbound WhatsApp text to test the full flow.

Ghala’s public documentation does not define its incoming message JSON body. These are **illustrative placeholders only**, not a claim about Ghala’s actual payload:

```json
{
  "data": {
    "from": "255712345678",
    "text": "Habari, hii shati bei gani?",
    "id": "example-message-id",
    "timestamp": 1789700000,
    "name": "Example customer"
  }
}
```

For that example only, paths would be `data.from`, `data.text`, `data.id`, `data.timestamp`, `data.name`. Timestamp must be Unix seconds or ISO-8601 with timezone. Until the real mapping is verified, events are stored and fail visibly instead of guessing the sender or message. Only incoming text is processed automatically. Status events are retained but not interpreted because their body schema is also unverified.

Incoming requests require the documented Ghala HMAC signature over timestamp and exact request bytes, within five minutes. Delivery IDs and message IDs are deduplicated. Outgoing text and product images use `/api/v2/messages` and a stable `Idempotency-Key`. Free-form messages require an active 24-hour customer window; this version directs you to Ghala for approved template messages outside that window. An accepted message is not represented as delivered/read.

## Connect Snippe

1. Add the **sessions merchant bearer token** and webhook signing secret in Settings. Snippe’s sessions reference documents bearer JWT/token authorization; do not assume a mobile-payments SDK `snp_...` key has sessions access.
2. Save the app’s public HTTPS URL. Duka includes the shop-specific callback URL when creating a session.
3. After a real WhatsApp customer confirms their catalog quote, Duka reserves stock and creates a fixed-TZS mobile-money hosted checkout at `/api/v1/sessions` (minimum TZS 500).
4. The customer receives the hosted checkout URL through Ghala. A merchant can also obtain it from the live order details.
5. A signed `payment.completed` event must match the shop, order, unique payment key, amount and currency. Duka then fetches the stored Snippe session reference server-side and requires completed status and matching metadata before marking the order paid.

Browser redirects and customer messages never prove payment. Duplicate events cannot create repeated confirmation messages. Failed/expired payment events do not downgrade paid orders. Session creation retries use the same payment key. Sessions currently expire after one hour; automatic renewal, reservation expiry/release, cancellations and refunds are not implemented. Review expired or abandoned orders manually before a real pilot. Demo payment controls only work on demo conversations/orders and never call Snippe.

## Products and assistant

- Add a description and JPG/PNG/WebP photo when creating a product. Click an existing product’s image or plus icon to add/replace its photo.
- Uploads are limited to 5 MB and 20 megapixels, decoded, stripped of metadata, resized to fit 1600×1600 and saved as randomly named JPEGs. Uploaded files live in `media/`; they are excluded from the source archive.
- In **Assistant**, set the name, API key and actual shop policies. The assistant reads that shop’s prices, stock, descriptions, delivery fee and language preference (up to 200 catalog products).
- **Test before going live** calls the real AI using saved settings. Without an API key it shows a setup error. It never sends WhatsApp messages, reserves stock or creates payments.
- The Responses API uses `gpt-4.1-mini`, strict structured output and `store:false`. Relevant customer messages and catalog data are sent to OpenAI. Local messages and webhook bodies remain in the app database.
- A live order offer must name a valid in-stock product, integer quantity and delivery address. The server calculates a quote using database prices. Only an exact subsequent `CONFIRM` or `THIBITISHA` message places the order. Changed prices, stock shortages and quotes older than 30 minutes require a new quote. Duplicate confirmation jobs reserve stock once.
- **Take over**, or sending a personal reply, pauses AI and clears the old quote. Live personal replies can attach a product photo. Resume the assistant when ready; future incoming messages trigger replies. It can also hand off on a customer’s request.
- Agent output is still probabilistic. Review your policy text and test realistic Swahili/English questions before enabling replies. Database actions and payment states are validated by Python, not trusted to model-generated claims.

## Staff and roles

Owners can add staff from **Settings → Staff**: each gets their own sign-in with a role — Owner
(full access), Manager (everything except staff management), or Agent (conversations, orders,
read-only settings — no credentials, no catalog edits, no checkout/fulfillment actions). Every
role check is enforced server-side, not just hidden in the UI. Security-sensitive and
business-critical actions (login/logout, staff changes, product/settings changes, job retries) are
recorded in an audit log.

## Deployment and remaining pilot work

This runs on a Python-capable host, not a static site host. Use a production WSGI server, HTTPS, a stable encryption key, `DJANGO_DEBUG=0`, a strong `DJANGO_SECRET_KEY`, and appropriate `DJANGO_ALLOWED_HOSTS`. Run `collectstatic`; configure static and public product-media serving at `/static/` and `/media/`. Both providers must be able to reach the HTTPS callbacks; localhost cannot receive them. Configure any reverse proxy’s trusted HTTPS headers correctly. Webhook request bodies must be preserved exactly. Limit upload sizes at the reverse proxy as well. Full step-by-step guide: [`PRODUCTION_DEPLOYMENT.md`](PRODUCTION_DEPLOYMENT.md).

Since the last production-transformation pass, this project now has: PostgreSQL support (set `DATABASE_URL`, code auto-switches — SQLite stays the local-dev default), login throttling (`django-axes`), password reset (needs real SMTP in production — see `.env.example`), staff roles and an audit log, a proper order/payment/fulfillment state machine with automatic stock-reservation expiry, a payment-reconciliation command (`manage.py reconcile_payments`), structured JSON logging, a `/healthz/` endpoint, an optional Sentry hook, working backup/restore commands (`manage.py backup_data` / `restore_data` — a real restore drill has been run, see `DISASTER_RECOVERY.md`), a systematic cross-tenant test sweep, adversarial upload-security tests, a CI workflow (`.github/workflows/ci.yml`, ready but not yet connected to a remote), and a thin Playwright E2E smoke layer (`e2e/`). Still open, and requiring the platform owner (see [`OWNER_ACTION_REQUIRED.md`](OWNER_ACTION_REQUIRED.md)): real Ghala/Snippe/OpenAI accounts, hosting, a domain, an SMTP provider, and the billing/refund/subscription business decisions. This is a strong foundation for a controlled pilot, not yet a commercially launched service — see `FINAL_PRODUCTION_AUDIT.md` for the honest final status.

The archive excludes databases, uploaded photos, `.local-secret`, `.env`, bytecode and nested older extracted copies. An older `duka-python/duka-python/` directory in a local workspace is not part of this updated package.

## Validation

```powershell
python manage.py check --deploy
python manage.py makemigrations --check --dry-run
python manage.py test
ruff check shop duka
```

79 automated backend tests pass: the original merchant/account/order isolation checks plus encrypted write-only credentials, photo re-encoding and adversarial upload rejection (path traversal, SVG/XXE, decompression bombs, zero-byte files), signature freshness/tampering, duplicate webhooks, unconfigured mapping, Snippe session contract and server verification, live-vs-demo separation, AI product validation, explicit order confirmation, stock idempotency, failed quote delivery, cross-shop payment rejection (a systematic sweep across every tenant-scoped endpoint, not just spot checks), handover/takeover/manual-reply flows, failed-job retry, RBAC role enforcement and audit logging, the order/payment/fulfillment state machine and stock-reservation expiry, login throttling, password reset, and backup/restore. Provider requests in these tests are mocked; no real messages or payments were sent.

A thin Playwright E2E smoke test (`e2e/`) additionally covers signup → demo sale flow → product-detail dialog → Settings against the real running app (requires Node.js — see `e2e/README.md`).

Browser checks passed for saving settings without exposing credentials, the missing-AI-key error, photo upload and persistence, and all six routes at 1920×1080, 1440×900, 1366×768, 1024×768, 768×1024, 430×932, 390×844 and 360×800 without page overflow or JavaScript errors.

## Provider references

- [Ghala API reference](https://ghala.io/help-center/api-reference/ghala-api-reference)
- [Ghala webhook setup](https://ghala.io/help-center/getting-started/setting-up-whatsapp-webhooks)
- [Snippe sessions](https://docs.snippe.sh/docs/2026-01-25/sessions)
- [Snippe webhook signatures and events](https://docs.snippe.sh/docs/2026-01-25/webhooks)
- [OpenAI structured outputs](https://developers.openai.com/api/docs/guides/structured-outputs)
- [GPT-4.1 mini](https://developers.openai.com/api/docs/models/gpt-4.1-mini)
