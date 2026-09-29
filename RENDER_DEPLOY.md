# Deploying Duka to Render

This uses the `render.yaml` blueprint already committed at the repo root, which defines three
linked services: a managed PostgreSQL database, a web service (Gunicorn), and a background worker
(`process_jobs`). See `PRODUCTION_DEPLOYMENT.md` for the general (platform-agnostic) deployment
steps this maps onto.

## Steps

1. **Sign in to Render** (render.com) and connect your GitHub account if you haven't already.
2. **New → Blueprint**, then pick the `gatxn/DukaAutomation` repository. Render auto-detects
   `render.yaml` and shows you the three services it will create (`duka-db`, `duka-web`,
   `duka-worker`).
3. Click **Apply**. Render provisions the database first, then builds and deploys both services.
   The build command runs `pip install`, `collectstatic`, and `migrate` automatically — no manual
   migration step needed on first deploy.
4. Once `duka-web` shows "Live", open its URL (`https://duka-web-xxxx.onrender.com`) and confirm
   `/healthz/` returns `{"status": "ok", ...}`.
5. Go to `/signup/` and create your first shop, exactly as in local dev.
6. In Settings, save your public HTTPS URL (the Render URL from step 4) — this is what Ghala/Snippe
   webhooks will call. Then proceed with the Ghala/Snippe/OpenAI setup in `OWNER_ACTION_REQUIRED.md`
   — those credentials go in the app's own Settings screen, never in Render's environment
   variables.

## Two things to know before you rely on this

**Product photos will not survive a redeploy on Render's free plan.** Render's free web-service
filesystem is ephemeral — it resets on every deploy/restart, and `MEDIA_ROOT` (where product photos
live) is local disk by default. This matches the exact gap already flagged in
`PRODUCTION_DEPLOYMENT.md` item 4: before you rely on this for real merchants, either (a) add a
paid Render Disk mounted at `media/` (simplest, keeps the current code unchanged), or (b) migrate
to S3-compatible object storage (`django-storages`) if you want to run multiple web instances
later. Photos uploaded during testing on the free plan will disappear the next time you push a
commit — expect this, don't be alarmed by it.

**The worker service costs money on Render** — Render's free plan doesn't currently support
background worker services running continuously; `duka-worker` will need at least the paid Starter
plan to run `process_jobs` around the clock. Without it, webhooks are received and queued but never
processed (no AI replies, no outbound messages, no payment confirmation) — the app will look "stuck"
for customers, not broken. If you want to stay fully free while testing before committing to a
paid tier, you can run `process_jobs` yourself against the same `DATABASE_URL` from your own
machine temporarily (copy the `DATABASE_URL` value from Render's `duka-db` dashboard into your
local `.env`), but that's a stopgap, not a real deployment.

## Redeploying after a code change

Push to `main` on GitHub — Render's blueprint auto-deploy picks it up (the same push also triggers
the GitHub Actions CI workflow already in this repo; check both go green).
