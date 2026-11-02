import pytest
from _support import close_all


@pytest.fixture(autouse=True)
def _close_scratch_stores():
    """Close every scratch store after each test so file descriptors do not accumulate."""
    yield
    close_all()
