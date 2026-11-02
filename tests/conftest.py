import pytest

from apple_mail_mcp import core


@pytest.fixture(autouse=True)
def require_mocked_mail_automation(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError("Mail automation must be mocked in unit tests")

    monkeypatch.setattr(core, "_popen_factory", blocked)
