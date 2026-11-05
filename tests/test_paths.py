import os
import subprocess
import sys
from pathlib import Path

import pytest


def test_notes_directory_can_live_outside_public_repository(tmp_path):
    env = os.environ.copy()
    env["SAP_SF_NOTES_DIR"] = str(tmp_path / "private-notes")
    output = subprocess.check_output(
        [sys.executable, "-c", "import ask; print(ask.NOTES_DIR)"],
        cwd=Path(__file__).parents[1],
        env=env,
        text=True,
    ).strip()

    assert output == str(tmp_path / "private-notes")


def test_capture_script_is_disabled_without_explicit_configuration():
    import ask

    assert ask.SAP_DRAFT_SCRIPT is None


def test_default_notes_and_index_paths_do_not_follow_working_directory(tmp_path):
    env = os.environ.copy()
    env.pop("SAP_SF_NOTES_DIR", None)
    project = Path(__file__).parents[1]
    env["PYTHONPATH"] = str(project)
    result = subprocess.check_output(
        [sys.executable, "-c", "import ask; print(ask.NOTES_DIR); print(ask.INDEX_DIR)"],
        cwd=tmp_path,
        env=env,
        text=True,
    ).splitlines()

    assert result == [str(Path.home() / ".local/share/sap-sf-assistant/notes"), str(project / "index")]
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("outcome", ["success", "failure", "timeout", "missing-cli"])
def test_codex_answer_scratch_is_private_and_cleaned_on_every_exit(tmp_path, monkeypatch, outcome):
    import ask

    notes = tmp_path / "private-notes"
    monkeypatch.setattr(ask, "NOTES_DIR", notes)
    captured = []

    def fake_run(command, **kwargs):
        answer = Path(command[command.index("-o") + 1])
        assert answer.parent.parent == notes / "tmp/codex-answers"
        assert answer.parent.is_dir()
        answer.write_text("synthetic private answer", encoding="utf-8")
        captured.append(answer)
        if outcome == "timeout":
            raise subprocess.TimeoutExpired(command, kwargs["timeout"])
        if outcome == "missing-cli":
            raise FileNotFoundError("synthetic missing CLI")
        return subprocess.CompletedProcess(command, 0 if outcome == "success" else 1, "", "synthetic failure")

    monkeypatch.setattr(ask.subprocess, "run", fake_run)
    result = ask.call_codex("synthetic question", [], {})

    assert result == "synthetic private answer" if outcome == "success" else result.startswith("[错误]")
    assert captured and not captured[0].parent.exists()
    assert list((notes / "tmp/codex-answers").iterdir()) == []
