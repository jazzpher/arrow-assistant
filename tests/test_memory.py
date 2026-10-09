from arrow_assistant.memory import Memory


def _mem(tmp_path):
    return Memory(db_path=str(tmp_path / "m.db"), md_dir=str(tmp_path))


def test_record_and_recall(tmp_path):
    m = _mem(tmp_path)
    m.record("excel.exe", "how do I export?", "Click File > Export.")
    m.record("excel.exe", "and to PDF?", "Pick PDF in the format list.")
    tail = m.recall("excel.exe")
    assert "how do I export?" in tail
    assert "and to PDF?" in tail
    m.close()


def test_recall_scoped_per_app(tmp_path):
    m = _mem(tmp_path)
    m.record("excel.exe", "excel question", "excel answer")
    m.record("chrome.exe", "chrome question", "chrome answer")
    assert "chrome question" not in m.recall("excel.exe")
    m.close()


def test_recall_empty(tmp_path):
    m = _mem(tmp_path)
    assert m.recall("nothing.exe") == ""
    m.close()


def test_markdown_tail_written(tmp_path):
    m = _mem(tmp_path)
    m.record("word.exe", "q1", "a1")
    md = (tmp_path / "word.exe.md").read_text(encoding="utf-8")
    assert "**You:** q1" in md and "**Arrow:** a1" in md
    m.close()


def test_recall_limit(tmp_path):
    m = _mem(tmp_path)
    for i in range(20):
        m.record("app.exe", f"q{i}", f"a{i}")
    tail = m.recall("app.exe", limit=5)
    assert "q19" in tail and "q15" in tail
    assert "q14" not in tail
    m.close()
