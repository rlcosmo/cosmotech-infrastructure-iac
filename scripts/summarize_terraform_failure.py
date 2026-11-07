"""Print allowlisted diagnostic identifiers, never raw Terraform output."""
import json
from pathlib import Path
import re
import sys


def summarize(text):
    text = re.sub(r'\x1b\[[0-9;]*m', '', text)
    return {
        'aws_operations': sorted(set(re.findall(r'operation error ([A-Za-z0-9 ]+): ([A-Za-z0-9]+)', text))),
        'denied_actions': sorted(set(re.findall(r'not authorized to perform:?\s+([a-z0-9-]+:[A-Za-z0-9]+)', text))),
        'aws_error_codes': sorted(set(re.findall(r'api error ([A-Za-z0-9]+):', text))),
        'terraform_errors': [name for name in ['Unsupported attribute', 'Invalid function argument',
            'Error acquiring the state lock', 'Inconsistent dependency lock file', 'Invalid index',
            'Invalid count argument', 'Invalid for_each argument', 'Reference to undeclared resource'] if name in text],
        'raw_output_withheld': True,
    }


if __name__ == '__main__':
    print(json.dumps(summarize(Path(sys.argv[1]).read_text())))
