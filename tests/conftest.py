import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import pytest
from config import Settings
from state import Store

@pytest.fixture
def settings():
    return Settings(_env_file=None,worker_api_token='test-secret-'*6)

@pytest.fixture
def store(tmp_path):
    return Store(tmp_path/'reviews.sqlite3')
