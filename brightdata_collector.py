"""Managed Bright Data Facebook group collection over HTTP."""
import json
import os
import re
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse
import requests

BASE_URL = 'https://api.brightdata.com/datasets/v3'
DEFAULT_DATASET_ID = 'gd_lz11l67o2cb3r0lkj3'

def provider_error(response, token, path):
    """Expose validation details while excluding credentials and echoed inputs."""
    detail = ''
    try:
        body = response.json()
        if isinstance(body, dict):
            for key in ('message', 'error', 'errors', 'detail', 'description'):
                value = body.get(key)
                if isinstance(value, str):
                    detail = value
                    break
                if isinstance(value, dict):
                    detail = str(value.get('message') or value.get('description') or '')
                    if detail:
                        break
        elif isinstance(body, str):
            detail = body
    except ValueError:
        pass
    if not detail:
        detail = {400: 'Request rejected: check dataset ID, input fields, dates and limits.',
                  401: 'Invalid or expired API key.',
                  403: 'Account or API key lacks access; check permissions and account status.',
                  402: 'Insufficient credit or billing authorization.',
                  429: 'Provider rate limit reached.'}.get(response.status_code, 'Provider request failed; inspect the collection in its dashboard.')
    if token:
        detail = detail.replace(token, '[REDACTED]')
    detail = re.sub(r'(?i)Bearer\s+\S+', 'Bearer [REDACTED]', detail)
    detail = re.sub(r'(?i)((?:api[_-]?(?:key|token)|authorization|password)\s*[=:]\s*)[^\s,;]+', r'\1[REDACTED]', detail)
    endpoint = path.strip('/').split('/')[0]
    return f'Bright Data HTTP {response.status_code} at {endpoint}: {detail[:800]}'

class CollectionPending(RuntimeError):
    def __init__(self, snapshot_id):
        super().__init__('Bright Data collection is still running; call /trigger again to resume it.')
        self.snapshot_id = snapshot_id

def parse_date(value):
    try:
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(value / (1000 if value > 10**12 else 1), timezone.utc)
        dt = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        return dt.astimezone(timezone.utc) if dt.tzinfo else None
    except (ValueError, TypeError, OverflowError, OSError):
        return None

def normalize_posts(records, group_url, start, end, limit):
    if not isinstance(records, list):
        raise RuntimeError('Bright Data snapshot must contain a JSON array of posts')
    items = {}
    group_path = urlparse(group_url).path.rstrip('/')
    for raw in records:
        if not isinstance(raw, dict):
            raise RuntimeError('Bright Data returned a malformed record')
        if raw.get('error') or raw.get('error_code'):
            raise RuntimeError('Bright Data returned a collection error record; inspect this snapshot in its dashboard')
        posted = parse_date(raw.get('date_posted'))
        if posted is None:
            raise RuntimeError('Bright Data post has no usable date_posted; recent-post filtering cannot be verified')
        if not start <= posted <= end:
            continue
        post_url = raw.get('url') or raw.get('post_url') or ''
        parsed = urlparse(post_url)
        if parsed.hostname not in ('facebook.com', 'www.facebook.com', 'm.facebook.com'):
            raise RuntimeError('Bright Data returned a non-Facebook post URL')
        post_id = raw.get('post_id')
        if not post_id:
            raise RuntimeError('Bright Data post is missing post_id')
        # Match IDs from the previous collector, including vanity group slugs.
        key = group_path + '/' + str(post_id)
        attachments = raw.get('attachments') or []
        if raw.get('post_image'):
            attachments = list(attachments) if isinstance(attachments, list) else [attachments]
            attachments.append(raw['post_image'])
        items[key] = {'postId': key, 'url': post_url, 'date': posted.isoformat(),
                      'authorName': raw.get('user_username_raw') or raw.get('user_name') or 'Unknown Author',
                      'text': raw.get('content') or '', 'attachments': attachments}
    return sorted(items.values(), key=lambda item: item['date'], reverse=True)[:limit]

def clear_pending(state_path):
    (state_path / 'brightdata_pending.json').unlink(missing_ok=True)

def fetch_posts(group_url, state_path, minutes=15, limit=10):
    token = os.getenv('BRIGHTDATA_API_TOKEN') or os.getenv('BRIGHTDATA_API_KEY')
    if not token:
        raise ValueError('Missing BRIGHTDATA_API_TOKEN in .env (BRIGHTDATA_API_KEY is also accepted)')
    dataset = os.getenv('BRIGHTDATA_DATASET_ID') or DEFAULT_DATASET_ID
    parsed = urlparse(group_url)
    if parsed.hostname not in ('facebook.com', 'www.facebook.com', 'm.facebook.com') or not re.fullmatch(r'/groups/[^/]+/?', parsed.path):
        raise ValueError('FACEBOOK_GROUP_URL must be a Facebook group URL')
    if minutes < 1 or limit < 1:
        raise ValueError('LOOKBACK_MINUTES and MAX_POSTS must be positive')
    wait_seconds = int(os.getenv('BRIGHTDATA_WAIT_SECONDS', '45'))
    if not 0 <= wait_seconds <= 300:
        raise ValueError('BRIGHTDATA_WAIT_SECONDS must be between 0 and 300')
    pending_file = state_path / 'brightdata_pending.json'
    with requests.Session() as session:
        session.headers.update({'Authorization': f'Bearer {token}'})
        def api(method, path, **kwargs):
            try:
                response = session.request(method, BASE_URL + path, timeout=30, **kwargs)
            except requests.RequestException:
                raise RuntimeError('Bright Data connection failed; check network access and retry') from None
            if not response.ok:
                raise RuntimeError(provider_error(response, token, path))
            try:
                return response.json()
            except ValueError:
                raise RuntimeError('Bright Data returned invalid JSON') from None
        if pending_file.exists():
            pending = json.loads(pending_file.read_text(encoding='utf8'))
            if pending['group_url'] != group_url or pending['dataset_id'] != dataset:
                raise RuntimeError('A pending collection belongs to a different group or dataset; finish it using the previous configuration first')
        else:
            end = datetime.now(timezone.utc)
            start = end - timedelta(minutes=minutes)
            # Provider dates select calendar days; enforce minutes on date_posted.
            result = api('POST', '/trigger',
                         params={'dataset_id': dataset, 'format': 'json', 'include_errors': 'true'},
                         json={'input': [{'url': group_url, 'num_of_posts': limit,
                                          'start_date': start.strftime('%m-%d-%Y'),
                                          'end_date': end.strftime('%m-%d-%Y')}],
                               'limit_per_input': limit})
            if not isinstance(result, dict) or not re.fullmatch(r'[A-Za-z0-9_-]+', str(result.get('snapshot_id', ''))):
                raise RuntimeError('Bright Data did not return a valid snapshot_id')
            pending = {'snapshot_id': result['snapshot_id'], 'group_url': group_url,
                       'dataset_id': dataset, 'start': start.isoformat(), 'end': end.isoformat(), 'limit': limit}
            temporary = pending_file.with_suffix('.tmp')
            temporary.write_text(json.dumps(pending), encoding='utf8')
            temporary.replace(pending_file)
        snapshot_id = pending['snapshot_id']
        if not re.fullmatch(r'[A-Za-z0-9_-]+', str(snapshot_id)):
            raise RuntimeError('Invalid saved Bright Data snapshot ID')
        deadline = time.monotonic() + wait_seconds
        while True:
            progress = api('GET', '/progress/' + snapshot_id)
            status = progress.get('status') if isinstance(progress, dict) else None
            if status == 'ready':
                records = api('GET', '/snapshot/' + snapshot_id, params={'format': 'json'})
                return normalize_posts(records, group_url, parse_date(pending['start']),
                                       parse_date(pending['end']), pending['limit'])
            if status == 'failed':
                clear_pending(state_path)
                raise RuntimeError('Bright Data collection failed; inspect the snapshot in its dashboard')
            if status not in ('starting', 'running', 'building'):
                raise RuntimeError('Bright Data returned an unexpected collection status')
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise CollectionPending(snapshot_id)
            time.sleep(min(5, remaining))
