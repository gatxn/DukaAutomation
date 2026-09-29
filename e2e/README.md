# Duka E2E smoke tests (Playwright)

Requires Node.js and the Django dev server running separately.

```bash
cd e2e
npm install
npx playwright install chromium
```

Start the Django app in another terminal first:

```bash
cd ..
python manage.py runserver
```

Then run the tests:

```bash
npm test
```

Set `DUKA_BASE_URL` to point at a different host (e.g. staging) instead of the default
`http://127.0.0.1:8000`.

This is a thin smoke layer, not a full E2E suite — it covers signup, the demo sale flow (create
order + simulate payment), the product-detail dialog, and a Settings round trip. See
`PRODUCTION_AUDIT.md` section N for the testing-gap context this was built to close.
