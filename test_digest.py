import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import digest


BASE = {"btc": 86000, "eth": 2700, "usd": 83, "usd_date": "27/09/2026",
        "jpy": 157, "ndx": 26000, "ndx_drawdown": -3}


class DigestTest(unittest.TestCase):
    def setUp(self):
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
