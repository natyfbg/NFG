"""Error alert emails: sent on a 500, throttled, and never carrying secrets."""

import pytest


@pytest.fixture
def failing(nfg_app, monkeypatch):
    sent = []
    monkeypatch.setattr(nfg_app, "ALERT_EMAIL", "alerts@example.com")
    monkeypatch.setattr(nfg_app.mailer, "configured", lambda: True)
    monkeypatch.setattr(
        nfg_app.mailer,
        "send",
        lambda to, subject, text, html=None: sent.append((to, subject, text)),
    )
    nfg_app.app.config.update(ALERTS_INLINE=True, PROPAGATE_EXCEPTIONS=False)

    def boom(**kwargs):
        raise RuntimeError("mongodb+srv://user:hunter2@cluster0.example.mongodb.net")

    monkeypatch.setitem(nfg_app.app.view_functions, "about_page", boom)
    yield sent
    nfg_app.app.config.pop("ALERTS_INLINE", None)
    nfg_app.app.config["PROPAGATE_EXCEPTIONS"] = None
    nfg_app.db.error_alerts.delete_many({})


def test_a_500_sends_one_alert_without_secrets(client, failing):
    resp = client.get("/about")
    assert resp.status_code == 500
    assert len(failing) == 1
    to, subject, text = failing[0]
    assert to == "alerts@example.com" and "RuntimeError" in subject and "/about" in subject
    assert "test_alerts.py" in text and "in boom" in text
    assert "hunter2" not in text + subject and "mongodb" not in text + subject


def test_repeat_errors_are_throttled(client, failing):
    for _ in range(4):
        assert client.get("/about").status_code == 500
    assert len(failing) == 1


def test_no_alert_without_an_address(client, failing, nfg_app, monkeypatch):
    monkeypatch.setattr(nfg_app, "ALERT_EMAIL", "")
    client.get("/about")
    assert failing == []
