from arrow_assistant.agent import privacy


def test_notice_mentions_cloud_and_training():
    assert "cloud" in privacy.NOTICE and "improve" in privacy.NOTICE


def test_ack_flow(tmp_path, monkeypatch):
    monkeypatch.delenv(privacy.ENV_OK, raising=False)
    monkeypatch.setattr(privacy, "ack_path", lambda: tmp_path / "a.txt")
    assert not privacy.acknowledged()
    out = []
    assert not privacy.ensure_console(input_fn=lambda _: "no", out=out.append)
    assert out and "PRIVACY" in out[0]
    assert privacy.ensure_console(input_fn=lambda _: "yes", out=out.append)
    assert privacy.acknowledged()


def test_stack_refuses_until_ack(monkeypatch, tmp_path):
    monkeypatch.delenv(privacy.ENV_OK, raising=False)
    monkeypatch.setattr(privacy, "ack_path", lambda: tmp_path / "a.txt")
    assert not privacy.acknowledged()
