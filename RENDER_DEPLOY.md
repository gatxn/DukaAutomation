# Deploying Duka to Render

This uses the `render.yaml` blueprint already committed at the repo root, which defines two linked
services: a web service (Gunicorn) and a background worker (`process_jobs`). Both the database and
product-photo storage are hosted on **Supabase**, not Render — see "Setting up Supabase" below for
why and how. See `PRODUCTION_DEPLOYMENT.md` for the general (platform-agnostic) deployment steps
this maps onto.

## Setting up Supabase (database + photo storage)

Render's free plan has two real gaps for this app: its Postgres free tier expires after 90 days,
and its filesystem doesn't reliably persist uploaded product photos even between requests to the
same running instance (confirmed directly against a live deploy — not a Duka code bug). Supabase's
free tier solves both: a persistent Postgres database, and an S3-compatible Storage bucket for
photos that survives redeploys.

1. **Create a Supabase project** at supabase.com (free tier). Pick a region close to your Render
   region to minimize latency.
2. **Database connection string**: in the Supabase dashboard, go to Settings → Database →
   Connection string → URI, and switch the mode to **Transaction** (pooled — required, since
   Render's services connect over IPv4 and Supabase's direct connection is IPv6-only). Copy that
   URI; you'll paste it into Render as `DATABASE_URL` in the next section. It already contains your
   database password — treat it like any other secret.
3. **Storage bucket**: in the Supabase dashboard, go to Storage, create a new bucket named
   `duka-media`, and mark it **Public** (product photos need to be viewable by shoppers without
   authentication).
4. **S3 access keys for that bucket**: Settings → Storage → S3 Connection (or "Access Keys" under
   Storage settings, depending on your Supabase version) → generate a new access key pair. You'll
   get an endpoint URL, access key ID, and secret access key.
5. **Your project's public URL**: Settings → API → Project URL (looks like
   `https://xxxxx.supabase.co`). This is `SUPABASE_PUBLIC_URL` below — it's how the app builds the
   public link to each uploaded photo.

## Setting up OTP delivery (Resend + a platform Ghala team)

Signup and password reset both verify the account via a one-time code, sent by email (Resend) or
WhatsApp (Ghala) — the user picks which. Leaving either unconfigured doesn't break the app; that
channel just shows a clean "not configured yet" error until you set it up. Full detail in
`OWNER_ACTION_REQUIRED.md` items 7 and 7b — short version:

1. **Resend** (resend.com): verify a sending domain, generate an API key. `RESEND_FROM_EMAIL` must
   use that verified domain, or real recipients (anyone outside your own test addresses) won't
   receive anything.
2. **A second, platform-level Ghala team** (v2.ghala.io) — **not** the per-merchant Ghala
   connection any individual shop owner sets up in their own Settings screen (step 7 below).
   This is a Ghala account *you* own, purely to send OTP codes. Create it, connect a WhatsApp
   Business number, mint a team API key (Settings → Developer → API Keys), and submit a WhatsApp
   "Authentication" template for Meta's approval — this last step is slow (real review time, out
   of your control) and blocks WhatsApp OTP from working until it clears, even once every env var
   below is set correctly.

## Deploying to Render

1. **Sign in to Render** (render.com) and connect your GitHub account if you haven't already.
2. **New → Blueprint**, then pick the `gatxn/DukaAutomation` repository. Render auto-detects
   `render.yaml` and shows you the two services it will create (`duka-web`, `duka-worker`).
3. Before clicking Apply, Render will prompt for the env vars marked `sync: false` in the
   blueprint — paste in directly (never relay these through anyone else, including an AI assistant):
   - `DATABASE_URL` — the Supabase Transaction-mode connection string from step 2 above.
   - `SUPABASE_S3_ENDPOINT`, `SUPABASE_S3_ACCESS_KEY_ID`, `SUPABASE_S3_SECRET_ACCESS_KEY` — from
     step 4 above.
   - `SUPABASE_S3_BUCKET` — `duka-media` (or whatever you named the bucket in step 3).
   - `SUPABASE_PUBLIC_URL` — from step 5 above.
   Both `duka-web` and `duka-worker` need all six; the blueprint lists them on both services.
   - `RESEND_API_KEY` and `GHALA_API_KEY`/`GHALA_OTP_TEMPLATE_NAME` — only `duka-web` needs these
     (OTP sends happen inline in the web request, not in the worker). `GHALA_API_KEY` here is the
     platform's own team key from step 2 above — a different credential from the per-shop Ghala
     token any merchant enters later in their own Settings screen (step 7 below).
4. Click **Apply**. Render builds and deploys both services. The build command runs `pip install`,
   `collectstatic`, and `migrate` automatically against your Supabase database — no manual migration
   step needed on first deploy.
5. Once `duka-web` shows "Live", open its URL (`https://duka-web-xxxx.onrender.com`) and confirm
   `/healthz/` returns `{"status": "ok", ...}`.
6. Go to `/signup/` and create your first shop, exactly as in local dev.
7. In Settings, save your public HTTPS URL (the Render URL from step 5) — this is what Ghala/Snippe
   webhooks will call. Then proceed with the Ghala/Snippe/OpenAI setup in `OWNER_ACTION_REQUIRED.md`
   — those credentials go in the app's own Settings screen, never in Render's environment
   variables.

## One thing to know before you rely on this

**The worker service costs money on Render** — Render's free plan doesn't currently support
background worker services running continuously; `duka-worker` will need at least the paid Starter
plan to run `process_jobs` around the clock. Without it, webhooks are received and queued but never
processed (no AI replies, no outbound messages, no payment confirmation) — the app will look "stuck"
for customers, not broken. If you want to stay fully free while testing before committing to a
paid tier, you can run `process_jobs` yourself against the same Supabase `DATABASE_URL` from your
own machine temporarily (paste it into your local `.env`), but that's a stopgap, not a real
deployment.

## Redeploying after a code change

Push to `main` on GitHub — Render's blueprint auto-deploy picks it up (the same push also triggers
the GitHub Actions CI workflow already in this repo; check both go green).
