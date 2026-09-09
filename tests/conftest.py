import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from agentstate.models import StateRecordInput
from agentstate.storage import sqlite_store


@pytest.fixture
def store(tmp_path):
    s = sqlite_store.connect(str(tmp_path / "state.db"))
    yield s
    s.close()


@pytest.fixture
def project(store):
    return store.get_or_create_project("github.com/acme/repo", name="repo")
