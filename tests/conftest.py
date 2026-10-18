import os
import tempfile
from pathlib import Path

import pytest

os.environ["JOBRADAR_DATA_DIR"] = tempfile.mkdtemp(prefix="jobradar-test-")
os.environ["JOBRADAR_DISABLE_SCHEDULER"] = "1"

FIXTURES = Path(__file__).parent / "fixtures"

RESUME = """Alex Kim
alex@example.com | Seattle, WA

EXPERIENCE
Software Engineer, Acme Analytics            Jun 2023 - Present
Built data pipelines in Python and SQL on AWS, orchestrated with Airflow.
Developed React and TypeScript dashboards backed by a FastAPI REST API and PostgreSQL.
Containerized services with Docker and deployed to Kubernetes via GitHub Actions.

EDUCATION
University of Washington, B.S. Computer Science, 2019 - 2023
"""


@pytest.fixture
def fixture_text():
    return lambda name: (FIXTURES / name).read_text()


@pytest.fixture
def db():
    from jobradar.db import get_engine, init_db, session_scope
    from jobradar.models import Base

    Base.metadata.drop_all(get_engine())
    init_db()
    with session_scope() as session:
        yield session
