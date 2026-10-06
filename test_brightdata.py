import json
import os
import tempfile
import unittest
from pathlib import Path
from datetime import datetime, timedelta, timezone
from unittest.mock import patch, MagicMock
import requests
from starlette.requests import Request
import monitor
import brightdata_collector as collector

GROUP = 'https://www.facebook.com/groups/123'
ENV = {'BRIGHTDATA_API_TOKEN': 'test-key', 'BRIGHTDATA_WAIT_SECONDS': '0',
       'FACEBOOK_GROUP_URL': GROUP, 'TELEGRAM_BOT_TOKEN': 'test', 'TELEGRAM_CHAT_ID': 'test'}

def record(post_id='456', date=None, media=True):
    return {'post_id': post_id, 'url': GROUP + '/posts/' + post_id + '/',
            'date_posted': date or datetime.now(timezone.utc).isoformat(),
            'user_username_raw': 'A & B', 'content': 'A < B',
            'attachments': ['image'] if media else []}

def response(data, status=200):
    result = MagicMock()
    result.ok = status < 400
    result.status_code = status
    result.json.return_value = data
    return result

class BrightDataTests(unittest.TestCase):
    def test_normalizes_schema_filters_date_and_keeps_existing_ids(self):
        end = datetime.now(timezone.utc)
        records = [record(date=end.isoformat()), record('old', (end - timedelta(hours=1)).isoformat()),
                   record('future', (end + timedelta(minutes=1)).isoformat())]
        items = collector.normalize_posts(records, GROUP, end - timedelta(minutes=10), end, 10)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]['postId'], '/groups/123/456')
        self.assertEqual(items[0]['authorName'], 'A & B')
        self.assertEqual(items[0]['text'], 'A < B')
        self.assertEqual(items[0]['attachments'], ['image'])
        raw = record(date=end.isoformat(), media=False)
        raw['post_image'] = 'photo'
        self.assertEqual(collector.normalize_posts([raw], GROUP, end-timedelta(minutes=10), end, 10)[0]['attachments'], ['photo'])

    def test_unknown_dates_and_error_records_fail(self):
        now = datetime.now(timezone.utc)
        for raw in (record(date='unknown'), {'error': 'blocked'}, {'error_code': 'failed'}):
            with self.assertRaises(RuntimeError):
                collector.normalize_posts([raw], GROUP, now - timedelta(minutes=10), now, 10)

    def test_pending_resumes_same_paid_job_and_original_time_window(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, ENV), patch.object(collector.requests, 'Session') as factory:
            session = factory.return_value.__enter__.return_value
            session.request.side_effect = [response({'snapshot_id': 's_test'}), response({'status': 'running'})]
            with self.assertRaises(collector.CollectionPending):
                collector.fetch_posts(GROUP, Path(folder), 15, 10)
            pending = json.loads((Path(folder) / 'brightdata_pending.json').read_text())
            post_time = pending['end']
            session.request.side_effect = [response({'status': 'ready'}), response([record(date=post_time)])]
            items = collector.fetch_posts(GROUP, Path(folder), 15, 10)
            self.assertEqual(len(items), 1)
            self.assertEqual(sum(call.args[0] == 'POST' for call in session.request.call_args_list), 1)
            payload = session.request.call_args_list[0].kwargs['json']
            self.assertEqual(payload['limit_per_input'], 10)
            self.assertEqual(payload['input'][0]['num_of_posts'], 10)

    def test_http_error_does_not_expose_api_key(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, ENV), patch.object(collector.requests, 'Session') as factory:
            factory.return_value.__enter__.return_value.request.return_value = response({'message': 'Invalid token test-key'}, 401)
            with self.assertRaisesRegex(RuntimeError, 'HTTP 401') as error:
                collector.fetch_posts(GROUP, Path(folder))
            self.assertNotIn('test-key', str(error.exception))

    def test_validation_error_exposes_endpoint_and_provider_reason(self):
        error = collector.provider_error(response({'error': 'Invalid start_date format'}, 400), 'secret', '/trigger')
        self.assertIn('HTTP 400 at trigger', error)
        self.assertIn('Invalid start_date format', error)
        error = collector.provider_error(response({'message': 'Bearer other-secret invalid'}, 403), 'secret', '/progress/s_test')
        self.assertNotIn('other-secret', error)

    def test_missing_credentials_prevent_paid_request(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {'BRIGHTDATA_API_TOKEN': '', 'BRIGHTDATA_API_KEY': ''}), patch.object(collector.requests, 'Session') as session:
            with self.assertRaises(ValueError):
                collector.fetch_posts(GROUP, Path(folder))
            session.assert_not_called()

    def test_failed_snapshot_clears_pending(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, ENV), patch.object(collector.requests, 'Session') as factory:
            factory.return_value.__enter__.return_value.request.side_effect = [response({'snapshot_id': 's_test'}), response({'status': 'failed'})]
            with self.assertRaisesRegex(RuntimeError, 'collection failed'):
                collector.fetch_posts(GROUP, Path(folder))
            self.assertFalse((Path(folder) / 'brightdata_pending.json').exists())

    def test_route_returns_count_and_pending_status(self):
        request = Request({'type': 'http', 'headers': [(b'x-trigger-secret', b'test-secret')]})
        with patch.dict(os.environ, {'TRIGGER_SECRET': 'test-secret'}), patch.object(monitor, 'trigger_scrape', return_value={'status': 'success', 'messages_sent': [{'success': True}, {'success': True}]}):
            self.assertEqual(monitor.trigger_route(request), 2)
        with patch.dict(os.environ, {'TRIGGER_SECRET': 'test-secret'}), patch.object(monitor, 'trigger_scrape', return_value={'status': 'success', 'messages_sent': []}):
            self.assertEqual(monitor.trigger_route(request), 0)
        pending = {'status': 'pending', 'snapshot_id': 's_test'}
        with patch.dict(os.environ, {'TRIGGER_SECRET': 'test-secret'}), patch.object(monitor, 'trigger_scrape', return_value=pending):
            self.assertEqual(monitor.trigger_route(request), pending)

    def test_telegram_retry_dedup_and_media_filter(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {**ENV, 'STATE_DIR': folder}), patch.object(monitor, 'fetch_posts') as fetch, patch.object(monitor, 'send_telegram', side_effect=[False, True]) as send, patch.object(monitor.time, 'sleep'):
            now = datetime.now(timezone.utc)
            fetch.return_value = collector.normalize_posts([record(date=now.isoformat()), record('789', now.isoformat(), False)], GROUP, now-timedelta(minutes=10), now, 10)
            pending = Path(folder) / 'brightdata_pending.json'
            pending.write_text('{}')
            self.assertEqual(monitor.trigger_scrape()['status'], 'partial_failure')
            self.assertTrue(pending.exists())
            self.assertEqual(monitor.trigger_scrape()['messages_sent'], [{'id': '/groups/123/456', 'success': True}])
            self.assertFalse(pending.exists())
            self.assertEqual(monitor.trigger_scrape()['messages_sent'], [])
            self.assertEqual(send.call_count, 2)
            self.assertIn('A &lt; B', send.call_args.args[2])

    def test_pending_does_not_send_telegram(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {**ENV, 'STATE_DIR': folder}), patch.object(monitor, 'fetch_posts', side_effect=collector.CollectionPending('s_test')), patch.object(monitor, 'send_telegram') as send:
            self.assertEqual(monitor.trigger_scrape()['status'], 'pending')
            send.assert_not_called()

if __name__ == '__main__':
    unittest.main()
