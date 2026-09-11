import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import bridge


class RefreshTests(unittest.TestCase):
    def test_failed_refresh_preserves_published_files(self):
        with tempfile.TemporaryDirectory() as folder:
            feed, status = Path(folder) / 'feed.json', Path(folder) / 'status.json'
            feed.write_text('previous feed')
            status.write_text('previous status')
            with patch('bridge.fetch_json', side_effect=bridge.BridgeError('upstream_timeout')):
                with self.assertRaises(bridge.BridgeError):
                    bridge.update(3, 100, 4, feed, status)
            self.assertEqual(feed.read_text(), 'previous feed')
            self.assertEqual(status.read_text(), 'previous status')

    def test_total_transfer_timeout_is_used(self):
        with patch('bridge.Path.is_file', return_value=True), patch('bridge._fetch_with_system_curl', return_value={'data': []}) as fetch:
            result = bridge.fetch_json(bridge.META_URL, timeout=12, max_bytes=10000)
            self.assertEqual(result, {'data': []})
            fetch.assert_called_once_with(bridge.META_URL, timeout=12, max_bytes=10000)

    def test_empty_download_cannot_replace_previous_feed(self):
        with tempfile.TemporaryDirectory() as folder:
            feed, status = Path(folder) / 'feed.json', Path(folder) / 'status.json'
            feed.write_text('previous feed')
            responses = [{'data': [{'source': 'data-20260910.json'}]}, {'listObjects': []}]
            with patch('bridge.fetch_json', side_effect=responses):
                with self.assertRaisesRegex(bridge.BridgeError, 'no_target_notices'):
                    bridge.update(1, 100, 4, feed, status)
            self.assertEqual(feed.read_text(), 'previous feed')
            self.assertFalse(status.exists())
