"""Generated content keeps its reply when it holds characters PostgreSQL refuses."""

import json

from app.core.metadata_db import storable_text


def test_nul_and_lone_surrogates_become_placeholders_everywhere():
    bad = "a\x00b\ud800c\udfffd"
    value = {"content": bad, bad: [bad, (bad, 1)], "n": 2, "none": None, "ok": "😀"}
    clean = "a�b�c�d"
    assert storable_text(value) == {
        "content": clean, clean: [clean, [clean, 1]], "n": 2, "none": None, "ok": "😀",
    }
    # What jsonb would refuse with SQLSTATE 22P05 is gone from the encoded form.
    encoded = json.dumps(storable_text(value))
    assert "\\u0000" not in encoded and "\\ud800" not in encoded


def test_storable_text_leaves_clean_values_identical():
    value = {"rows": [["North", 1.5, True]], "text": "Revenue → up"}
    assert storable_text(value) == value
    assert storable_text("plain") == "plain"
    assert storable_text(3) == 3
