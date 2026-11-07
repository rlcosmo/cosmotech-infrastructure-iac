import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('recovery', Path(__file__).parents[1] / 'scripts/recover_lab_v2_document.py')
recovery = importlib.util.module_from_spec(spec)
spec.loader.exec_module(recovery)

class DocumentRecoveryTests(unittest.TestCase):
    def test_recovery_requires_matching_active_private_document(self):
        attrs = {'id':recovery.NAME,'name':recovery.NAME,'document_type':'Command','content':'{"schemaVersion":"2.2"}'}
        doc = {'Name':recovery.NAME,'DocumentType':'Command','Status':'Active','Content':attrs['content']}
        recovery.verify(attrs, doc, {})
        for bad in [dict(doc, Status='Failed'), dict(doc, Content='{}'), dict(doc, Name='other')]:
            with self.assertRaises(ValueError): recovery.verify(attrs, bad, {})
        with self.assertRaises(ValueError): recovery.verify(attrs, doc, {'AccountIds':['all']})
