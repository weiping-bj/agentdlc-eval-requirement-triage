import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from s12_eval_helpers import GOLDEN  # noqa: E402


@pytest.fixture
def golden_rows():
    with open(GOLDEN, encoding="utf-8") as f:
        return [json.loads(l) for l in f if l.strip()]
