"""Exercise actual ASGI HTTP requests without sockets or provider calls."""
import json
import os
import unittest
from unittest.mock import patch
import monitor

async def get_trigger(headers=None, query=b''):
    events = []
    scope = {'type': 'http', 'asgi': {'version': '3.0'}, 'http_version': '1.1',
             'method': 'GET', 'scheme': 'https', 'path': '/trigger', 'raw_path': b'/trigger',
             'query_string': query, 'headers': headers or [],
             'server': ('testserver', 443), 'client': ('127.0.0.1', 1234)}
    async def receive():
        return {'type': 'http.request', 'body': b'', 'more_body': False}
    async def send(event):
        events.append(event)
    await monitor.app(scope, receive, send)
    status = next(event['status'] for event in events if event['type'] == 'http.response.start')
    body = b''.join(event.get('body', b'') for event in events if event['type'] == 'http.response.body')
    return status, json.loads(body)

class AuthTests(unittest.IsolatedAsyncioTestCase):
    async def test_missing_wrong_and_query_secrets_never_collect(self):
        with patch.dict(os.environ, {'TRIGGER_SECRET': 'test-secret'}), patch.object(monitor, 'trigger_scrape') as collect:
            for headers, query in (([], b''), ([(b'x-trigger-secret', b'wrong')], b''),
                                   ([], b'trigger_secret=test-secret')):
                status, body = await get_trigger(headers, query)
                self.assertEqual(status, 401)
                self.assertEqual(body, {'detail': 'Unauthorized'})
            collect.assert_not_called()

    async def test_unconfigured_server_fails_closed(self):
        with patch.dict(os.environ, {'TRIGGER_SECRET': ''}), patch.object(monitor, 'trigger_scrape') as collect:
            status, body = await get_trigger([(b'x-trigger-secret', b'anything')])
            self.assertEqual(status, 503)
            collect.assert_not_called()

    async def test_authorized_request_preserves_integer_response(self):
        with patch.dict(os.environ, {'TRIGGER_SECRET': 'test-secret'}), patch.object(monitor, 'trigger_scrape',
             return_value={'status': 'success', 'messages_sent': [{'success': True}]}) as collect:
            status, body = await get_trigger([(b'x-trigger-secret', b'test-secret')])
            self.assertEqual(status, 200)
            self.assertEqual(body, 1)
            collect.assert_called_once()

if __name__ == '__main__':
    unittest.main()
