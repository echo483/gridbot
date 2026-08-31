"""Tests for scripts/dispatch.py pair-mode evidence handling.

2026-08-30: a pair dispatch of the H-MAPENTRY1 verdict spec produced nothing
recoverable. Three compounding defects, none of which surfaced as an error:

  1. `--out` is accepted by the parser but never passed to pair(), so the
     relay trail was silently never written.
  2. pair() discards the builder's stdout (`rc, _ = _run(...)`). A lane that
     returns CODE AS TEXT — which every OpenRouter lane does, since
     `hermes -z --safe-mode` cannot write to the repo — has its entire work
     product thrown away.
  3. The artifact guard is `not Path(a).exists()`. Naming an artifact that
     ALREADY exists (the normal case when extending a file) passes trivially,
     so a builder that wrote nothing is indistinguishable from success.

Together: ~35 minutes of lane time, no artifact, no text, no error. These
tests pin the evidence contract so that cannot recur silently.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import dispatch


def _stub_runs(monkeypatch, outputs):
    """Replace _run with a scripted sequence, recording the lanes called."""
    calls: list[str] = []
    seq = list(outputs)

    def fake_run(kind, prompt, timeout):
        calls.append(kind)
        return seq.pop(0) if seq else (0, "")

    monkeypatch.setattr(dispatch, "_run", fake_run)
    return calls


def test_pair_writes_the_relay_trail_to_out(tmp_path, monkeypatch):
    """--out must actually receive the trail; it was silently dropped."""
    spec = tmp_path / "spec.md"
    spec.write_text("build the thing")
    artifact = tmp_path / "thing.py"
    artifact.write_text("original\n")
    out = tmp_path / "trail.md"

    def fake_run(kind, prompt, timeout):
        if kind == "programming":
            artifact.write_text("rewritten by builder\n")
            return 0, "BUILDER SAID THIS"
        return 0, "REVIEWER FINDINGS HERE"

    monkeypatch.setattr(dispatch, "_run", fake_run)

    rc = dispatch.pair(str(spec), [str(artifact)], 60, "review2", "programming",
                       out=str(out))

    assert rc == 0
    trail = out.read_text()
    assert "REVIEWER FINDINGS HERE" in trail
    assert "BUILDER SAID THIS" in trail, "builder output must survive into the trail"


def test_pair_preserves_builder_text_when_it_writes_no_file(tmp_path, monkeypatch):
    """The OpenRouter lanes return code as TEXT and cannot write to the repo.
    That text is the work product and must not be discarded."""
    spec = tmp_path / "spec.md"
    spec.write_text("build it")
    artifact = tmp_path / "exists_already.py"
    artifact.write_text("untouched\n")
    out = tmp_path / "trail.md"

    _stub_runs(monkeypatch, [(0, "def solution():\n    return 42\n"), (0, "review text")])

    dispatch.pair(str(spec), [str(artifact)], 60, "review2", "programming",
                  out=str(out))

    assert "def solution():" in out.read_text()
    assert artifact.read_text() == "untouched\n"


def test_pair_reports_an_unchanged_artifact_instead_of_passing(tmp_path, monkeypatch, capsys):
    """An artifact that already existed and was NOT modified means the builder
    produced nothing. `exists()` cannot tell those apart; a digest can."""
    spec = tmp_path / "spec.md"
    spec.write_text("build it")
    artifact = tmp_path / "already_here.py"
    artifact.write_text("pre-existing content\n")

    _stub_runs(monkeypatch, [(0, "text only, no files"), (0, "review")])

    dispatch.pair(str(spec), [str(artifact)], 60, "review2", "programming")

    err = capsys.readouterr().err
    assert "UNCHANGED" in err, "an unmodified pre-existing artifact must be called out"


def test_pair_still_detects_a_genuinely_missing_artifact(tmp_path, monkeypatch, capsys):
    """The original guard's real case must keep working."""
    spec = tmp_path / "spec.md"
    spec.write_text("build it")
    missing = tmp_path / "never_created.py"

    _stub_runs(monkeypatch, [(0, "nothing"), (0, "review")])

    rc = dispatch.pair(str(spec), [str(missing)], 60, "review2", "programming")

    assert rc == 1
    assert "did not produce" in capsys.readouterr().err
