import json
from pathlib import Path


def test_official_macro_data_sources_file_is_valid_json():
    file_path = Path(__file__).resolve().parent.parent / "macro_data_sources.official.json"
    payload = json.loads(file_path.read_text(encoding="utf-8"))

    assert isinstance(payload, list)
    assert len(payload) >= 6
    assert any(item.get("provider") == "fred" for item in payload)
    assert any(item.get("provider") == "bls" for item in payload)
