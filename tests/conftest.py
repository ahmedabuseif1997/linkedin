import shutil
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SITE_URL = "https://example.com/"


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """A throwaway copy of the repository's site/ directory."""
    shutil.copytree(REPO / "site", tmp_path / "site")
    return tmp_path
