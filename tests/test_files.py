import json

from gridbot.capture.files import DayWriter


def test_append_roundtrip(tmp_path):
    w = DayWriter(tmp_path, "test")
    w.append({"a": 1})
    w.append({"b": {"nested": True}})
    w.close()
    files = list(tmp_path.glob("test-*.jsonl"))
    assert len(files) == 1
    rows = [json.loads(line) for line in files[0].read_text().splitlines()]
    assert rows[0]["a"] == 1 and "ts_ns" in rows[0]
    assert rows[1]["b"] == {"nested": True}
    assert w.written == 2
