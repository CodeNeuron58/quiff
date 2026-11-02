import asyncio

import pytest


@pytest.fixture
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(autouse=True)
def isolated_local_appdata(tmp_path, monkeypatch):
    """No test may touch the real machine's app data (Notepad's saved tabs live
    under LOCALAPPDATA); every test gets an empty, throwaway one."""
    fake = tmp_path / "localappdata"
    fake.mkdir()
    monkeypatch.setenv("LOCALAPPDATA", str(fake))
    return fake
