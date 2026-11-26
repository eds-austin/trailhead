import pytest

from trailhead.data.db import connect
from trailhead.data.repo import Repos


@pytest.fixture
def repos(tmp_path):
    conn = connect(tmp_path / "t.db")
    yield Repos.open(conn)
    conn.close()
