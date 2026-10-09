from arrow_assistant.kb import lookup


def test_lookup_hit(tmp_path):
    (tmp_path / "granta.exe.md").write_text("# Granta docs\nClick X.", encoding="utf-8")
    assert "Granta docs" in lookup("granta.exe", str(tmp_path))


def test_lookup_case_insensitive(tmp_path):
    (tmp_path / "app.exe.md").write_text("docs", encoding="utf-8")
    assert lookup("APP.EXE", str(tmp_path)) == "docs"


def test_lookup_miss(tmp_path):
    assert lookup("nope.exe", str(tmp_path)) is None


def test_lookup_empty_app(tmp_path):
    assert lookup("", str(tmp_path)) is None
