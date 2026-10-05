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

    def test_daily_ready_report_and_retry(self):
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "state.json"
            baseline = Path(directory) / "baseline.json"
            baseline.write_text(json.dumps({key: [] for key in digest.CHANNELS}))
            now = datetime(2026, 10, 5, 9, 15, tzinfo=ZoneInfo("Europe/Moscow"))
            video = {"id": "Co8EMfLkVoc", "channel": "Тест"}
            with patch.object(digest, "STATE", state_path), patch.object(digest, "VIDEO_BASELINE", baseline), patch.object(digest, "recent_videos", return_value=[video]), patch.object(digest, "send") as send, patch.object(digest, "analyze_video", side_effect=RuntimeError("API")):
                self.assertEqual(digest.check_new_videos(digest.load_state(), now), 0)
                state = digest.load_state()
                self.assertIn(video['id'], state['video_pending'])
                self.assertNotIn(video['id'], state.get('video_report_ids', []))
                self.assertEqual(digest.check_new_videos(state, now), 0)
            with patch.object(digest, "STATE", state_path), patch.object(digest, "VIDEO_BASELINE", baseline), patch.object(digest, "recent_videos", return_value=[video]), patch.object(digest, "send") as send, patch.object(digest, "analyze_video", return_value="Готовый проверенный отчёт"):
                self.assertEqual(digest.check_new_videos(digest.load_state(), now.replace(day=6)), 1)
                send.assert_called_once_with("Готовый проверенный отчёт")
                self.assertEqual(digest.load_state()['video_pending'], {})
                self.assertEqual(digest.check_new_videos(digest.load_state(), now.replace(day=7)), 0)

    def test_recovers_previous_link_notification(self):
        with tempfile.TemporaryDirectory() as directory:
            baseline = Path(directory) / 'baseline.json'
            baseline.write_text(json.dumps({key: [] for key in digest.CHANNELS}))
            state = {'version': 2, 'video_seen': {next(iter(digest.CHANNELS)): ['Co8EMfLkVoc']}}
            now = datetime(2026, 10, 5, 9, tzinfo=ZoneInfo('Europe/Moscow'))
            with patch.object(digest, 'STATE', Path(directory) / 'state.json'), patch.object(digest, 'VIDEO_BASELINE', baseline), patch.object(digest, 'recent_videos', return_value=[]), patch.object(digest, 'analyze_video', return_value='Отчёт'), patch.object(digest, 'send') as send:
                self.assertEqual(digest.check_new_videos(state, now), 1)
                send.assert_called_once_with('Отчёт')


if __name__ == "__main__":
    unittest.main()
