"""Start behind the existing HTTPS proxy; credential supplied by secret runtime."""
import os
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / "src"))
from iac_portal.server import serve

serve(Path(os.environ["IAC_REPORT_DIRECTORY"]), root / "ops/iac-portal/index.html",
      os.environ["IAC_CREDENTIAL_SHA256"])
