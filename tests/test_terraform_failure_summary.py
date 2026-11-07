import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('summary', Path(__file__).parents[1] / 'scripts/summarize_terraform_failure.py')
summary = importlib.util.module_from_spec(spec)
spec.loader.exec_module(summary)

class FailureSummaryTests(unittest.TestCase):
    def test_only_diagnostic_identifiers_leave_private_log(self):
        result = summary.summarize('private-password operation error EC2: DescribeInstances, api error UnauthorizedOperation: not authorized to perform: ec2:DescribeInstances on private-resource')
        self.assertEqual(result['denied_actions'], ['ec2:DescribeInstances'])
        self.assertEqual(result['aws_error_codes'], ['UnauthorizedOperation'])
        self.assertNotIn('private', str(result))
