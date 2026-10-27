import importlib.util
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('failure_log', Path(__file__).parent/'skills/collect-avito/scripts/failure_log.py')
failure_log = importlib.util.module_from_spec(spec)
spec.loader.exec_module(failure_log)


class FailureBudgetTests(unittest.TestCase):
    def test_retry_budget_is_shared_across_agents_urls_and_stages(self):
        with tempfile.TemporaryDirectory() as directory:
            diag = Path(directory)
            first = failure_log.record(diag, 'filters', 'No suggestions', 'choose city', 'https://www.avito.ru/moskva/vakansii', 'Luna')
            self.assertEqual(first['retry_budget_remaining'], 1)
            with self.assertRaises(failure_log.HumanRequired):
                failure_log.ensure_allowed(diag, 'filters')
            failure_log.ensure_allowed(diag, 'capture', 'Fresh snapshot found a corrected control')
            second = failure_log.record(diag, 'capture', 'Still unavailable', 'choose city', 'https://www.avito.ru/all/vakansii', 'Terra')
            self.assertEqual(second['state'], 'NEEDS_HUMAN')
            for stage in ['filters', 'freeze', 'capture', 'publish']:
                with self.assertRaises(failure_log.HumanRequired):
                    failure_log.ensure_allowed(diag, stage, 'Another agent tries')
            self.assertTrue((diag/'failure-report.md').exists())
            self.assertEqual(len(failure_log.load(diag)['events']), 3)

    def test_started_retry_cannot_be_silently_restarted_after_interruption(self):
        with tempfile.TemporaryDirectory() as directory:
            diag = Path(directory)
            failure_log.record(diag, 'capture', 'Connection lost', 'read card')
            failure_log.ensure_allowed(diag, 'capture', 'Connection restored')
            with self.assertRaisesRegex(failure_log.HumanRequired, 'already started'):
                failure_log.ensure_allowed(diag, 'capture', 'Connection restored again')

    def test_access_block_stops_immediately_without_retry(self):
        with tempfile.TemporaryDirectory() as directory:
            diag = Path(directory)
            event = failure_log.record(diag, 'capture', 'CAPTCHA', 'open card', immediate=True)
            self.assertEqual(event['retry_budget_remaining'], 0)
            with self.assertRaises(failure_log.HumanRequired):
                failure_log.ensure_allowed(diag, 'capture', 'Not permitted')

    def test_successful_correction_allows_progress_without_resetting_budget(self):
        with tempfile.TemporaryDirectory() as directory:
            diag = Path(directory)
            failure_log.record(diag, 'filters', 'Wrong trigger', 'open city')
            failure_log.ensure_allowed(diag, 'filters', 'Observed the jobs-specific trigger')
            failure_log.succeeded(diag, 'filters', 'Dialog visible')
            failure_log.ensure_allowed(diag, 'filters')
            event = failure_log.record(diag, 'filters', 'Another failure', 'select profession')
            self.assertEqual(event['state'], 'NEEDS_HUMAN')


if __name__ == '__main__':
    unittest.main()
