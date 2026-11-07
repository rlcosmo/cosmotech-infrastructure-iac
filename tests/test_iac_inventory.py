import unittest
from src.iac_portal.inventory import collect


class InventoryTests(unittest.TestCase):
    def test_untagged_resources_and_metadata_only(self):
        calls = []
        def call(service, operation, *args):
            calls.append((service, operation, args))
            if service == "ecr":
                return {"repositories": [{"repositoryArn": "arn:untagged"}]}
            if operation == "list-queues":
                return {"QueueUrls": ["https://sqs.example/q"]}
            if operation == "get-queue-attributes":
                return {"Attributes": {"QueueArn": "arn:queue"}}
            raise RuntimeError("Denied")
        result = collect(call, "us-east-1")
        self.assertEqual({r["arn"] for r in result["resources"]}, {"arn:untagged", "arn:queue"})
        self.assertFalse(result["complete"])
        self.assertIn({"service": "lambda", "status": "unavailable"}, result["coverage"])
        self.assertTrue(all(operation not in {"get-secret-value", "receive-message"} for _, operation, _ in calls))

    def test_denial_never_claims_empty_account(self):
        def denied(*args):
            raise RuntimeError("denied")
        result = collect(denied, "us-east-1")
        self.assertFalse(result["complete"])
        self.assertTrue(all(x["status"] == "unavailable" for x in result["coverage"]))

    def test_sqs_request_budget_is_bounded(self):
        calls = []
        def call(service, operation, *args):
            if operation == "list-queues":
                return {"QueueUrls": [str(i) for i in range(201)]}
            if operation == "get-queue-attributes":
                calls.append(args)
                return {"Attributes": {"QueueArn": "arn:" + args[1]}}
            raise RuntimeError("unavailable")
        result = collect(call, "us-east-1")
        self.assertEqual(len(calls), 200)
        self.assertIn({"service": "sqs", "status": "partial"}, result["coverage"])
