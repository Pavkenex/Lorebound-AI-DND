"""Lantern-primer tutorial structure + /content/tutorial route."""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.content.tutorial import INTRODUCTION
from app.main import app

client = TestClient(app, raise_server_exceptions=False)


def _text(blob: dict) -> str:
    parts = [str(blob.get(k, "")) for k in ("title", "text")]
    for beat in blob.get("beats", []):
        parts.append(str(beat.get("title", "")))
        parts.append(str(beat.get("text", "")))
        for choice in beat.get("choices", []):
            parts.append(str(choice.get("label", "")))
    return "\n".join(parts).lower()


def test_introduction_structure():
    assert INTRODUCTION["id"] == "lantern-primer"
    assert INTRODUCTION["title"]
    beats = INTRODUCTION["beats"]
    assert len(beats) >= 5
    assert len(beats) >= 6 - 1  # about six beats
    for beat in beats:
        assert beat.get("id") and beat.get("text")


def test_introduction_references_marla_and_a_check():
    blob = _text(INTRODUCTION)
    assert "marla" in blob
    assert "aric" in blob
    assert "lantern inn" in blob
    assert "check" in blob
    assert "missing travelers" in blob
    mini = [b for b in INTRODUCTION["beats"] if "choice" in b["id"]]
    assert mini, "expected a mini-choice beat"
    assert len(mini[0].get("choices", [])) >= 2


def test_content_tutorial_route():
    r = client.get("/content/tutorial")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["id"] == "lantern-primer"
    assert len(body["beats"]) >= 5
    assert "marla" in _text(body)
