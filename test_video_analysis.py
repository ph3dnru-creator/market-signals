import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import video_analysis as va

VIDEO = {'id': 'Co8EMfLkVoc', 'channel': 'Тест', 'url': 'https://www.youtube.com/watch?v=Co8EMfLkVoc'}
REPORT = dict(title='Название', verdict='Полезно', theses='Тезисы', facts='Факты [1]', take='Берём', skip='Не берём', strategy='Без изменений', sources=['https://example.org/source'], plan_changes=[])
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
            with patch('video_transcript.fetch_transcript', return_value=transcript), patch.object(va, 'generate', return_value=(REPORT, GROUNDING)) as generate:
                message = va.analyze_video(VIDEO, directory)
                self.assertEqual(generate.call_count, 1)
                self.assertIn('Влияние на стратегию', message)
            with patch.object(va, 'generate', side_effect=AssertionError('Cache must prevent API calls')):
                self.assertEqual(va.analyze_video(VIDEO, directory), message)

    def test_incomplete_transcript_is_not_saved_as_report(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch('video_transcript.fetch_transcript', return_value={'complete': False, 'transcript': 'x' * 500}):
                with self.assertRaises(va.AnalysisError):
                    va.analyze_video(VIDEO, directory)
            self.assertFalse((Path(directory) / (VIDEO['id'] + '.json')).exists())

if __name__ == '__main__':
    unittest.main()

class ProposalGuardTest(unittest.TestCase):
    def test_unready_broker_and_ungrounded_evidence_cannot_send_buy(self):
        import json,copy
        from signals import load_plan
        plan=load_plan()
        change={'row_id':'neo','side':'Купить','amount_rub':100000,'min_price':17,'max_price':18,'reason':'fact','evidence_urls':['https://example.org/source']}
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/(VIDEO['id']+'.json')
            data={'plan_version':plan['version'],'report':{'plan_changes':[change]},'grounding':GROUNDING}
            path.write_text(json.dumps(data))
            self.assertIsNone(va.plan_proposal(VIDEO,directory,plan))
            ready=copy.deepcopy(plan);ready['broker_ready']=True
            data['report']['plan_changes'][0]['evidence_urls']=['https://invented.org']
            path.write_text(json.dumps(data))
            with self.assertRaises(va.AnalysisError):va.plan_proposal(VIDEO,directory,ready)

    def test_zero_amount_cancellation_is_concrete_and_not_applied(self):
        import json,copy
        from signals import load_plan
        plan=load_plan();before=copy.deepcopy(plan)
        data={'plan_version':plan['version'],'report':{'plan_changes':[{'row_id':'neo','side':'Не покупать','amount_rub':0,'min_price':None,'max_price':None,'reason':'Новое снижение прогноза','evidence_urls':['https://example.org/source']}]},'grounding':GROUNDING}
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory)/(VIDEO['id']+'.json')).write_text(json.dumps(data))
            key,message=va.plan_proposal(VIDEO,directory,plan)
            self.assertIn('0 ₽',message)
            self.assertIn('План пока не изменён',message)
            self.assertEqual(plan,before)
