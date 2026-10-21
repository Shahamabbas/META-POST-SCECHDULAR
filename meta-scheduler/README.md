# Social Studio — Meta Post Scheduler

A complete, locally runnable **single-owner** scheduling application with an English dashboard. Python 3.11+ and a modern browser are all you need; there are no pip/npm dependencies. Meta OAuth, access tokens, uploads to Meta, and actual publishing are deliberately left to you in `integration.py`.

## Start in two minutes

Extract the ZIP, open a terminal in `meta-scheduler`, then:

**Windows PowerShell**

```powershell
$env:APP_PASSWORD = Read-Host 'Choose a workspace password (12+ characters)'
python app.py
```

**macOS / Linux (Bash)**

```bash
read -rsp 'Choose a workspace password (12+ characters): ' APP_PASSWORD
export APP_PASSWORD
python3 app.py
```

Open **http://127.0.0.1:8080**, sign in with that password, then add up to three destinations under **Accounts**. Each Facebook Page or Instagram professional account counts as one destination. A brand with both platforms uses two destinations.

1. Add your destination names, platform and optional remote IDs.
2. Open **Create post**, select one to three destinations, enter your caption and upload optional media.
3. Choose date, time and timezone. Save a draft or schedule.
4. Check **Content queue**, **Calendar**, and **Activity**.

Instagram requires media. The app supports one JPEG, PNG or MP4 per post, up to 25 MB, and captions up to 2,200 characters. These are conservative app limits, not a statement of Meta API limits. Your adapter must validate current platform requirements. Carousels, stories and dedicated Reel controls are not implemented.

## What works without Meta

- Password sign-in, HttpOnly session cookies, CSRF protection and sign-out.
- Three saved account destinations with platform labels and remote identifiers.
- Media upload, private media retrieval and post preview.
- Draft creation, editing, duplication and scheduling.
- Timezone selection with UTC storage; daylight-saving gaps and ambiguous wall-clock times are rejected.
- Calendar, filtered content queue, dashboard counters and delivery activity.
- Independent per-account delivery records and persistent SQLite storage.
- Background dispatch, cancellation and explicit manual retry.
- Interrupted publishing becomes `unknown`; uncertain outcomes are never automatically retried.

Adding an account creates a local record. It does **not** connect or authorize Meta. When due, a post becomes **Integration required** until the adapter exists; it is never marked published by a mock implementation.

## Add your Meta API code

Edit **`integration.py` → `Publisher.publish()`**. This is the only required code boundary for actual publishing. The worker calls it once for each selected destination.

```python
class Publisher:
    def publish(self, *, account, caption, media, idempotency_key):
        # Retrieve credentials server-side using account['id'] / account['external_id'].
        # Validate media and upload/host it according to your chosen Meta API.
        # Reconcile any previously created request for this delivery key.
        # Publish, wait for confirmation, and return a real remote post ID.
        return confirmed_remote_post_id
```

Inputs:

| Input | Fields / meaning |
| --- | --- |
| `account` | `id`, `name`, `platform`, `external_id` |
| `caption` | Shared caption text |
| `media` | `None` or `id`, `name`, `mime`, `size`, absolute local `path` |
| `idempotency_key` | Stable delivery ID, retained across retries |

No token fields exist in the dashboard. Keep credentials in your own server-side secret store. Implement your own authorization flow and token refresh where needed. The scheduler intentionally contains no Meta endpoint/version/permission assumptions.

Exception contract:

- `IntegrationRequired`: configuration missing and **no request sent** → blocked.
- `PublishRejected`: definitive remote rejection and **no post created** → failed.
- Any other exception, including timeout → unknown; verify the remote account before retrying.
- Nonempty string return → confirmed published remote ID.

Persist Meta upload/container/request IDs in your integration storage keyed by `idempotency_key`. This key is local; it does not mean Meta supports native idempotency. Exact-once external publishing cannot be guaranteed without reconciling remote side effects. Use finite network timeouts in the adapter; the worker processes deliveries sequentially.

After changing the adapter, restart the server and manually retry blocked deliveries. Already published destinations are not resent. An interrupted request must be verified remotely first. Cancellation stops remaining work; it does not remove posts already published remotely.

## Data and operation

The browser can close while scheduling continues, but **the server must stay running**. Due posts are picked up every ~2 seconds. On restart, overdue pending posts are processed; interrupted in-flight deliveries are held for review. Saved sessions end on server restart or after 12 hours.

Run **one application server instance per database**. SQLite transaction claims prevent duplicate dispatch by concurrent claim attempts, but startup recovery assumes the prior instance has stopped. Back up the entire data directory with the server stopped. It contains the database, uploaded media, captions and activity.

Environment variables:

| Variable | Default | Purpose |
| --- | --- | --- |
| `APP_PASSWORD` | Required, 12+ characters | Workspace password |
| `HOST` | `127.0.0.1` | Bind address |
| `PORT` | `8080` | HTTP port |
| `DATA_DIR` | `./data` | Persistent database and media |
| `COOKIE_SECURE` | unset | Set `1` when served through HTTPS |

This is a single-owner local application, not a multi-user SaaS. To expose it online, put it behind HTTPS and access controls, add reverse-proxy request/rate limits, and enable `COOKIE_SECURE=1`. Password login has a fixed delay but no distributed brute-force protection. Preserve the original Host header when proxying. Deploy as one long-running service with durable disk, not static hosting or an ephemeral serverless function. Unused uploaded files are retained; delete them only after checking database references. Stop the server before manual database maintenance.

### Docker

```bash
docker build -t social-studio .
docker run --rm -p 127.0.0.1:8080:8080 -e APP_PASSWORD -v social-studio-data:/data social-studio
```

Export `APP_PASSWORD` in your shell first. The named volume retains data between containers. On Windows, set the environment variable in PowerShell as shown above.

## Verify

```bash
python3 -m unittest discover -s tests -v
```

Core tests cover three destinations, future times, drafts, Instagram media requirements, confirmed publication, blocked integration, definite rejection, uncertain results, concurrency claims, cancellation and restart recovery. HTTP tests cover authentication, CSRF, upload retrieval, scheduling and the three-account limit. Browser rendering has not been automatically verified.

## Source map

- `app.py` — HTTP API, SQLite storage, scheduling worker, authentication and uploads.
- `integration.py` — your Meta-specific adapter.
- `static/index.html` — English dashboard.
- `static/style.css` — responsive styling.
- `static/app.js` — forms, preview, timezone conversion, queue and calendar.
- `tests/` — standard-library test suite.
- `Dockerfile` — optional container deployment.

## API reference

All requests are same-origin. Authenticate using `POST /api/login` with `{password}`. It sets a session cookie. `GET /api/state` returns the CSRF token. All subsequent writes require `X-CSRF-Token`.

| Method / path | JSON fields |
| --- | --- |
| `GET /api/state` | Returns accounts, posts with deliveries, events and CSRF token |
| `POST /api/accounts` | `name`, `platform` (`facebook`/`instagram`), `external_id` (optional), optional `id` to edit name/remote ID |
| `POST /api/accounts/delete` | `id`; rejected if account has post history |
| `POST /api/media` | `name`, `data` (base64 without data-URL prefix) |
| `GET /media/<id>` | Authenticated image/video bytes |
| `POST /api/posts` | `caption`, `account_ids`, `media_id` or null, `timezone`, `draft`, `scheduled_at`; optional `id` edits an unprocessed post |
| `POST /api/posts/cancel` | `id`; cancels remaining deliveries |
| `POST /api/deliveries/retry` | `id`, `verified_not_published: true` required for unknown outcomes |
| `POST /api/logout` | `{}` |

`scheduled_at` must be an ISO 8601 timestamp with an explicit timezone, in the future, unless saving a draft. Post state stays `scheduled` after dispatch; the UI derives delivery completion from destination statuses. Session state is in memory; application data is persistent.
