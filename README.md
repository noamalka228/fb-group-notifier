# Facebook group → Telegram using Bright Data

The app calls Bright Data's managed Facebook **Posts by group URL** Scraper API
using HTTP. Run it on your computer or in a cloud container. No local browser,
Facebook login session, or browser installation is needed.

## Configuration

Keep your existing `.env` with `FACEBOOK_GROUP_URL`, `TELEGRAM_BOT_TOKEN`, and
`TELEGRAM_CHAT_ID`. Add your Bright Data API key locally:

```dotenv
BRIGHTDATA_API_TOKEN=your_api_key
BRIGHTDATA_DATASET_ID=gd_lz11l67o2cb3r0lkj3
BRIGHTDATA_WAIT_SECONDS=45
LOOKBACK_MINUTES=15
MAX_POSTS=10
STATE_DIR=state
```

`BRIGHTDATA_API_KEY` is accepted as an alternative to `BRIGHTDATA_API_TOKEN`.
The default dataset ID is the official Facebook **Posts by group URL** scraper,
verified against Bright Data's published schema. Do not use a profiles or
comments dataset. Keep credentials out of Git and logs. Existing Telegram
settings and delivered-post IDs are preserved.

The original defaults remain: 15-minute lookback, maximum 10 collected posts,
and posts must have media. Use `LOOKBACK_MINUTES=10` for a strict ten-minute
window. Overlapping windows are deduplicated using persistent SQLite IDs.
The original filter accepts attachments or post images; text-only posts are
skipped. Each qualifying post sends one Telegram text message with author,
publish time, text and link; media files themselves are not uploaded.

## Run

Use Python 3.10+:

```sh
python -m venv .venv
# Linux/macOS:
. .venv/bin/activate
# Windows PowerShell instead: .\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python monitor.py
```

Call `GET http://localhost:8000/trigger` with the `X-Trigger-Secret` header.

## Route authentication

Set `TRIGGER_SECRET` to a strong random secret, separate from provider and
Telegram keys. The migration generated one in your local `.env` if absent.
Copy that value into your cloud deployment's environment variables.

In cron-job.org, edit the job and add a custom HTTP header:

```text
Name: X-Trigger-Secret
Value: the value of TRIGGER_SECRET
```

Use your deployment's HTTPS `/trigger` URL. Keep the secret in the header,
never in the URL/query string. Restart/redeploy after changing environment
variables. Missing or incorrect headers return HTTP 401 before collection or
delivery begins. An unset/empty server secret disables the route with HTTP 503.
The one-shot CLI is local execution and does not require the HTTP header.
Any holder of the shared secret can trigger collection; rotate it if disclosed.
Authentication does not enforce a cooldown or spending limit.

- Success: a JSON integer, e.g. `2`, counting posts sent to Telegram.
- No qualifying/unsent posts: `0`.
- Collection not ready: `{"status":"pending","snapshot_id":"s_...","message":"..."}`.
- Errors or partial Telegram failure: a detailed status/error object.

The app starts an asynchronous collection, polls its progress for up to the
configured wait budget, then downloads JSON when ready. Each HTTP request has
a separate 30-second timeout, so total route latency can exceed the poll budget.
Set your proxy/server timeout accordingly, or use `BRIGHTDATA_WAIT_SECONDS=0`
to do one progress check per invocation. The snapshot ID and original filtering
window are saved in `state/brightdata_pending.json`; the next trigger resumes
that job instead of purchasing another collection. A pending response is not
a completed collection. The file is cleared only after successful Telegram
processing, or a provider-reported failed collection. Partial delivery retries
the same snapshot and skips posts already sent successfully.

## Schedule every ten minutes

One-shot CLI:

```sh
python monitor.py --once
```

Exit code is 0 for success or pending, 1 for failure. Linux cron:

```cron
*/10 * * * * cd /absolute/path/fb-group-notifier && /absolute/path/fb-group-notifier/.venv/bin/python monitor.py --once >> monitor.log 2>&1
```

Or schedule an HTTP call to `/trigger`. The existing database lock prevents
simultaneous collection/delivery on the same state directory. In a multi-instance
cloud deployment use a single worker or shared coordination; separate local
databases cannot prevent duplicate deliveries between instances.

## Docker/cloud

```sh
docker build -t fb-group-notifier .
docker run --env-file .env -p 8000:8000 -v notifier-state:/app/state fb-group-notifier
```

Use a persistent volume for `STATE_DIR`. Losing it loses duplicate tracking
and pending collection IDs. The server binds to localhost outside Docker and
to `0.0.0.0` in Docker. Protect `/trigger` with your cloud ingress authentication
before exposing it publicly: calling the route can incur provider charges.
Old saved browser/session files are no longer read and may be removed manually.

## Validation and limitations

```sh
python -m unittest -v test_brightdata.py test_auth.py
python test_run.py
```

Unit tests mock both providers and incur no charges. `test_run.py` makes a real
Bright Data request but sends no Telegram messages; it leaves the snapshot for
the next `/trigger` call to deliver.

Provider date filters request the relevant UTC calendar days using MM-DD-YYYY;
the app enforces the exact time interval locally using `date_posted`, never the
scrape timestamp. `num_of_posts` and `limit_per_input` cap collected records.
Filtering out old records or duplicates locally does not refund provider usage.
Raise `MAX_POSTS` for a busy group; a cap can miss recent posts. Unknown publish
dates, malformed snapshots and provider error records produce a failure.

The filtering window stays tied to when a collection started, so a slow result
is not discarded merely because downloading it took time. However, long jobs,
missed schedules, and outages can exceed the lookback overlap and leave gaps.
Delivery is best-effort: termination after Telegram accepts a message but before
the database commits can cause a duplicate on retry.

The account must have access to this scraper and sufficient credit. No live
collection was verified during migration because no Bright Data key was present.

Official references:
- https://docs.brightdata.com/scrapers/facebook.com.json
- https://docs.brightdata.com/api-reference/rest-api/scraper/asynchronous-requests
- https://docs.brightdata.com/api-reference/scrapers/management-apis/monitor-progress
