import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import digest


BASE = {"btc": 86000, "eth": 2700, "usd": 83, "usd_date": "27/09/2026",
        "jpy": 157, "ndx": 26000, "ndx_drawdown": -3}


class DigestTest(unittest.TestCase):
    def setUp(self):
        legacy = patch.object(digest, 'load_plan', return_value=None)
        legacy.start()
        self.addCleanup(legacy.stop)
        formatter = patch.object(digest, 'format_event', return_value='Approved action')
        formatter.start()
        self.addCleanup(formatter.stop)
        for name in ('check_new_videos', 'send_video_reports'):
            mock = patch.object(digest, name, return_value=0)
            mock.start()
            self.addCleanup(mock.stop)

    def test_first_run_silent_then_one_event(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(digest, "STATE", Path(directory) / "state.json"):
                with patch.object(digest, "send") as send:
                    with patch.object(digest, "fetch_quotes", return_value=BASE):
                        digest.main()
                        digest.main()
                    send.assert_not_called()
                    with patch.object(digest, "fetch_quotes", return_value=dict(BASE, usd=84.2)):
                        digest.main()
                        digest.main()
                    self.assertEqual(send.call_count, 1)
                    with patch.object(digest, "fetch_quotes", return_value=BASE):
                        digest.main()
                    self.assertEqual(send.call_count, 2)

    def test_observation_without_action_is_silent_and_saved(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(digest, "STATE", Path(directory) / "state.json"), patch.object(digest, "send") as send, patch.object(digest, "format_event", return_value=None):
                with patch.object(digest, "fetch_quotes", return_value=BASE):
                    digest.main()
                with patch.object(digest, "fetch_quotes", return_value=dict(BASE, usd=84.2)):
                    digest.main()
                    digest.main()
                send.assert_not_called()
                self.assertEqual(digest.load_state()["statuses"]["usd_high"], "active")

    def test_disconnect_is_retried(self):
        import http.client
        from unittest.mock import MagicMock
        response = MagicMock()
        response.__enter__.return_value.read.return_value = b"ok"
        with patch.object(digest.urllib.request, "urlopen", side_effect=[http.client.RemoteDisconnected(), response]) as request, patch.object(digest.time, "sleep"):
            self.assertEqual(digest.http_get("https://example.org"), b"ok")
            self.assertEqual(request.call_count, 2)

    def test_only_persistent_failure_alerts_once(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(digest, "STATE", Path(directory) / "state.json"), patch.object(digest, "TOKEN", "test"), patch.object(digest, "CHAT_ID", "test"), patch.object(digest, "send") as send:
                digest.record_failure()
                digest.record_failure()
                send.assert_not_called()
                digest.record_failure()
                digest.record_failure()
                self.assertEqual(send.call_count, 1)
                with patch.object(digest, "fetch_quotes", return_value=BASE):
                    digest.main()
                self.assertEqual(digest.load_state()["consecutive_failures"], 0)

    def test_failed_send_preserves_old_state_for_retry(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(digest, "STATE", Path(directory) / "state.json"):
                with patch.object(digest, "fetch_quotes", return_value=BASE):
                    with patch.object(digest, "send"):
                        digest.main()
                with patch.object(digest, "fetch_quotes", return_value=dict(BASE, usd=84.2)):
                    with patch.object(digest, "send", side_effect=RuntimeError("telegram")):
                        with self.assertRaises(RuntimeError):
                            digest.main()
                    self.assertEqual(digest.load_state()["statuses"]["usd_high"], "inactive")


if __name__ == "__main__":
    unittest.main()

class PlanDigestTest(unittest.TestCase):
    def setUp(self):
        from signals import load_plan
        from zoneinfo import ZoneInfo
        from datetime import datetime
        self.plan=load_plan()
        self.now=datetime(2026,11,28,10,tzinfo=ZoneInfo('Europe/Moscow'))

    def test_daily_check_and_plan_diff_are_not_replayed(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(digest,'STATE',Path(directory)/'state.json'), patch.object(digest,'send') as send, patch.object(digest,'fetch_plan_quotes',return_value={}), patch.object(digest,'check_new_videos') as videos:
                state=digest.load_state()
                digest.run_plan(state,self.plan,self.now)
                digest.run_plan(state,self.plan,self.now)
                self.assertEqual(send.call_count,1)
                self.assertEqual(videos.call_count,1)
                self.assertIn('обновлён план',send.call_args.args[0])

    def test_failed_plan_delivery_is_retried_without_losing_diff(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(digest,'STATE',Path(directory)/'state.json'),patch.object(digest,'send',side_effect=RuntimeError('network')):
                state=digest.load_state()
                with self.assertRaises(RuntimeError):digest.run_plan(state,self.plan,self.now)
                self.assertNotIn('plan_snapshot',state)
                self.assertNotIn('last_plan_check',state)

    def test_deadline_once_and_no_legacy_report_dispatch(self):
        from signals import plan_snapshot
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(digest,'STATE',Path(directory)/'state.json'),patch.object(digest,'send') as send,patch.object(digest,'fetch_plan_quotes',return_value={}),patch.object(digest,'check_new_videos'),patch.object(digest,'send_video_reports',side_effect=AssertionError('must not replay')):
                state={'version':2,'plan_snapshot':plan_snapshot(self.plan)}
                digest.run_plan(state,self.plan,self.now.replace(month=12,day=4))
                digest.run_plan(state,self.plan,self.now.replace(month=12,day=5))
                self.assertEqual(send.call_count,1)
                self.assertIn('2 782 322 ₽',send.call_args.args[0])
