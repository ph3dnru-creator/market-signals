import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

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


if __name__ == "__main__":
    unittest.main()
