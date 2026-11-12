import os
import pytest
from fastapi.testclient import TestClient
from app.db import make_engine, initialize
from app.models import Base
from app.main import create_app


@pytest.fixture
def engine(tmp_path):
    url = os.getenv('TEST_DATABASE_URL', f'sqlite:///{tmp_path}/test.db')
    if 'TEST_DATABASE_URL' in os.environ and 'test' not in url:
        raise RuntimeError('TEST_DATABASE_URL must identify a disposable test database (name contains test)')
    engine = make_engine(url)
    Base.metadata.drop_all(engine)
    initialize(engine)
    yield engine
    Base.metadata.drop_all(engine)
    engine.dispose()


@pytest.fixture
def client(engine):
    with TestClient(create_app(engine)) as client:
        yield client
