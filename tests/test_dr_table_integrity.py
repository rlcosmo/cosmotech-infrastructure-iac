from decimal import Decimal
from types import SimpleNamespace
import unittest
from unittest.mock import Mock
from src.disaster_recovery.table_integrity import collect, compare, digest, TABLES


class TableIntegrityTests(unittest.TestCase):
    def client(self, region, pages):
        client = Mock()
        client.meta = SimpleNamespace(region_name=region)
        client.get_paginator.return_value.paginate.return_value = pages
        return client

    def test_pagination_order_does_not_change_comparison_and_no_payload_saved(self):
        a = {'order_id': 'a', 'amount': Decimal('1.0')}
        b = {'order_id': 'b', 'tags': {'x', 'y'}}
        source = collect(self.client('us-east-1', [{'Items': [a]}, {'Items': [b]}]), TABLES['us-east-1'])
        target = collect(self.client('us-east-2', [{'Items': [b, a]}]), TABLES['us-east-2'])
        self.assertTrue(compare(source, target)['equal'])
        self.assertNotIn('a', source['rows'])
        self.assertEqual(digest(Decimal('1.0')), digest(Decimal('1.00')))

    def test_missing_extra_and_modified_records_are_distinguished(self):
        source = collect(self.client('us-east-1', [{'Items': [{'order_id': 'a'}, {'order_id': 'b', 'n': 1}]}]), TABLES['us-east-1'])
        target = collect(self.client('us-east-2', [{'Items': [{'order_id': 'b', 'n': 2}, {'order_id': 'c'}]}]), TABLES['us-east-2'])
        result = compare(source, target)
        self.assertFalse(result['equal'])
        self.assertEqual((result['missing'], result['extra'], result['changed']), (1, 1, 1))

    def test_foreign_table_rejected_before_any_scan(self):
        client = self.client('us-east-2', [])
        with self.assertRaises(ValueError):
            collect(client, TABLES['us-east-1'])
        client.get_paginator.assert_not_called()

    def test_changed_duplicate_key_aborts_non_snapshot_scan(self):
        client = self.client('us-east-1', [{'Items': [{'order_id': 'a', 'n': 1}]}, {'Items': [{'order_id': 'a', 'n': 2}]}])
        with self.assertRaises(ValueError):
            collect(client, TABLES['us-east-1'])
