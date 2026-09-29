import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

import digest


class VideoReportDeliveryTest(unittest.TestCase):
    def test_sends_once_and_recovers_after_send_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            reports = Path(directory) / "reports.jsonl"
            reports.write_text("\n".join(json.dumps({"id": key, "message": key})
                                         for key in ("first", "second")), encoding="utf-8")
            state_path = Path(directory) / "state.json"
            with patch.object(digest, "VIDEO_REPORTS", reports), patch.object(digest, "STATE", state_path):
                with patch.object(digest, "send", side_effect=[None, RuntimeError("network")]):
                    with self.assertRaises(RuntimeError):
                        digest.send_video_reports({"version": 2, "statuses": {}})
                self.assertEqual(digest.load_state()["video_report_ids"], ["first"])
                with patch.object(digest, "send") as send:
                    self.assertEqual(digest.send_video_reports(digest.load_state()), 1)
                    send.assert_called_once_with("second")
                    self.assertEqual(digest.send_video_reports(digest.load_state()), 0)

    def test_checks_channels_once_daily_and_only_notifies_new_videos(self):
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "state.json"
            baseline_path = Path(directory) / "baseline.json"
            baseline_path.write_text(json.dumps({key: ["old"] for key in digest.CHANNELS}), encoding="utf-8")
            now = datetime(2026, 9, 29, 9, 15, tzinfo=ZoneInfo("Europe/Moscow"))
            with patch.object(digest, "STATE", state_path), patch.object(digest, "VIDEO_BASELINE", baseline_path), \
                 patch.object(digest, "recent_videos", return_value=[{"id": "new", "channel": "Тест", "url": "https://example.com"}]) as recent, \
                 patch.object(digest, "send") as send:
                self.assertEqual(digest.check_new_videos(digest.load_state(), now), len(digest.CHANNELS))
                self.assertEqual(send.call_count, len(digest.CHANNELS))
                self.assertEqual(digest.check_new_videos(digest.load_state(), now), 0)
                self.assertEqual(recent.call_count, len(digest.CHANNELS))


if __name__ == "__main__":
    unittest.main()
