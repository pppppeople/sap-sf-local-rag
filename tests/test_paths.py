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
    assert ask.NLM_NOTEBOOKS == {}


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

    assert result.ok == (outcome == "success")
    if result.ok:
        assert result.content == "synthetic private answer"
    else:
        assert result.content is None
        assert result.error == {"failure": "process_error", "timeout": "timeout",
                                "missing-cli": "command_missing"}[outcome]
    assert captured and not captured[0].parent.exists()
    assert list((notes / "tmp/codex-answers").iterdir()) == []


@pytest.mark.parametrize("variable", ["SAP_SF_CODEX_PATH", "SAP_SF_NLM_PATH"])
def test_cli_environment_candidates_skip_missing_and_preserve_order(tmp_path, monkeypatch, variable):
    import ask

    first = tmp_path / "first-cli"
    second = tmp_path / "second-cli"
    first.touch()
    second.touch()
    monkeypatch.setenv(variable, os.pathsep.join(["", str(tmp_path / "missing"),
                                               str(first), str(second)]))
    assert ask._env_cli_path(variable) == str(first)


@pytest.mark.parametrize("location", ["environment", "path", "home", "unavailable"])
def test_notebooklm_discovery_precedence_and_portable_fallback(tmp_path, monkeypatch, location):
    import ask

    monkeypatch.delenv("SAP_SF_NLM_PATH", raising=False)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    home_cli = tmp_path / ".local/bin/nlm"
    if location != "unavailable":
        home_cli.parent.mkdir(parents=True)
        home_cli.touch()
    path_cli = tmp_path / "path-cli"
    monkeypatch.setattr(ask.shutil, "which", lambda command:
                        str(path_cli) if location in {"environment", "path"} else None)
    env_cli = tmp_path / "custom-cli"
    if location == "environment":
        env_cli.touch()
        monkeypatch.setenv("SAP_SF_NLM_PATH", str(env_cli))

    assert ask._find_nlm() == {"environment": str(env_cli), "path": str(path_cli),
                               "home": str(home_cli), "unavailable": "nlm"}[location]
