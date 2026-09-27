from pathlib import Path
import pytest

from recursive_discovery.core import Kernel, Ledger
from recursive_discovery.store import SQLiteLedger, BlobStore


@pytest.fixture(params=['jsonl', 'sqlite'])
def world(request, tmp_path):
    ledger = Ledger(tmp_path / 'science.jsonl') if request.param == 'jsonl' else SQLiteLedger(tmp_path / 'science.sqlite3')
    kernel = Kernel(tmp_path / 'kernel.key')
    blobs = BlobStore(tmp_path / 'blobs')
    yield ledger, kernel, blobs, tmp_path
    if hasattr(ledger, 'close'):
        ledger.close()
