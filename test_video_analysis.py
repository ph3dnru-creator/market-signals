import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import video_analysis as va

VIDEO = {'id': 'Co8EMfLkVoc', 'channel': 'Тест', 'url': 'https://www.youtube.com/watch?v=Co8EMfLkVoc'}
REPORT = dict(title='Название', verdict='Полезно', theses='Тезисы', facts='Факты [1]', take='Берём', skip='Не берём', strategy='Без изменений', sources=['https://example.org/source'])
GROUNDING = {'webSearchQueries': ['claim'], 'groundingChunks': [{'web': {'uri': 'https://example.org/source'}}]}

class AnalysisTest(unittest.TestCase):
    def test_requires_grounded_evidence(self):
        with self.assertRaises(va.AnalysisError):
            va.render(VIDEO, REPORT, {})
        with self.assertRaises(va.AnalysisError):
            va.render(VIDEO, dict(REPORT, sources=['https://invented.org']), GROUNDING)

    def test_full_pipeline_and_cached_delivery(self):
        with tempfile.TemporaryDirectory() as directory:
            transcript = {'complete': True, 'transcript': 'Полный текст. ' * 100}
            with patch.object(va, 'generate', side_effect=[(transcript, {}), (REPORT, GROUNDING)]) as generate:
                message = va.analyze_video(VIDEO, directory)
                self.assertEqual(generate.call_count, 2)
                self.assertIn('Влияние на стратегию', message)
            with patch.object(va, 'generate', side_effect=AssertionError('Cache must prevent API calls')):
                self.assertEqual(va.analyze_video(VIDEO, directory), message)

    def test_incomplete_transcript_is_not_saved_as_report(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(va, 'generate', return_value=({'complete': False, 'transcript': 'x' * 500}, {})):
                with self.assertRaises(va.AnalysisError):
                    va.analyze_video(VIDEO, directory)
            self.assertFalse((Path(directory) / (VIDEO['id'] + '.json')).exists())

if __name__ == '__main__':
    unittest.main()
