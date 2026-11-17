"""SF-2: synthetic boundary failures must never become persistent answers."""
import io
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from docx import Document

import ask
import kb_hook
import sap_asset
from lib.result import Result, OperationFailed, failure


@pytest.fixture(autouse=True)
def synthetic_notebook_configuration(monkeypatch):
    """The public export has no built-in notebook IDs; tests supply fiction."""
    monkeypatch.setattr(ask, 'NLM_NOTEBOOKS', {'platform': 'synthetic-platform'})


def test_notebooklm_requires_explicit_configuration(monkeypatch):
    monkeypatch.setattr(ask, 'NLM_NOTEBOOKS', {})
    monkeypatch.setattr(ask.subprocess, 'run', lambda *a, **k: pytest.fail('no configured notebook'))
    assert ask.ask_llm('question', 'notebooklm', {'providers': {}}).error == 'configuration_missing'


@pytest.mark.parametrize('provider', ['claude-code', 'codex', 'notebooklm'])
@pytest.mark.parametrize('outcome, code', [
    ('timeout', 'timeout'), ('missing', 'command_missing'),
    ('exit', 'process_error'), ('empty', 'empty_response'),
])
def test_cli_provider_failures_are_results(tmp_path, monkeypatch, provider, outcome, code):
    monkeypatch.setattr(ask, 'NOTES_DIR', tmp_path)

    def run(command, **kwargs):
        if outcome == 'timeout':
            raise subprocess.TimeoutExpired(command, 1, output='PRIVATE')
        if outcome == 'missing':
            raise FileNotFoundError('PRIVATE')
        output = json.dumps({'answer': ''}) if provider == 'notebooklm' else ''
        return SimpleNamespace(returncode=1 if outcome == 'exit' else 0,
                               stdout=output, stderr='PRIVATE')

    monkeypatch.setattr(ask.subprocess, 'run', run)
    result = ask.ask_llm('PRIVATE', provider, {'providers': {}})
    assert not result.ok and result.content is None and result.error == code
    assert 'PRIVATE' not in repr(result)


@pytest.mark.parametrize('payload, code', [
    ('PRIVATE malformed json', 'invalid_json'), ('[]', 'invalid_schema'),
    ('{"value": []}', 'invalid_schema'), ('{"answer": 1}', 'invalid_schema'),
])
def test_notebooklm_rejects_invalid_payload(monkeypatch, payload, code):
    monkeypatch.setattr(ask.subprocess, 'run', lambda *a, **k:
                        SimpleNamespace(returncode=0, stdout=payload))
    assert ask.call_notebooklm('question', {}).error == code


def test_claude_continuation_failure_does_not_accept_partial_answer(monkeypatch):
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        if len(calls) == 1:
            return SimpleNamespace(returncode=0, stdout='partial:', stderr='')
        raise subprocess.TimeoutExpired(command, 1)

    monkeypatch.setattr(ask.subprocess, 'run', run)
    assert ask.call_claude_code('question', []).error == 'timeout'
    assert len(calls) == 2


@pytest.mark.parametrize('provider', ['anthropic', 'openai'])
@pytest.mark.parametrize('error_name, code, retryable', [
    ('AuthenticationError', 'authentication_error', False),
    ('RateLimitError', 'rate_limit', True),
    ('APITimeoutError', 'timeout', True),
    ('APIConnectionError', 'connection_error', True),
    ('NotFoundError', 'model_not_found', False),
    ('RuntimeError', 'provider_error', False),
])
def test_sdk_errors_are_sanitized_results(monkeypatch, provider, error_name, code, retryable):
    error = type(error_name, (Exception,), {})('PRIVATE credential/body')

    def create(**kwargs):
        raise error

    client = SimpleNamespace(messages=SimpleNamespace(create=create),
                             chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    sdk = SimpleNamespace(Anthropic=lambda **k: client, OpenAI=lambda **k: client,
                          AuthenticationError=Exception, RateLimitError=Exception,
                          NotFoundError=Exception)
    monkeypatch.setitem(sys.modules, provider, sdk)
    result = ask.ask_llm('PRIVATE', provider, {'providers': {provider: {'api_key': 'synthetic'}}})
    assert result == failure(code, retryable)
    assert 'PRIVATE' not in repr(result)


@pytest.mark.parametrize('provider', ['anthropic', 'openai'])
def test_sdk_client_setup_failure_is_also_normalized(monkeypatch, provider):
    def create(**kwargs):
        raise RuntimeError('PRIVATE setup failure')
    monkeypatch.setitem(sys.modules, provider, SimpleNamespace(
        Anthropic=create, OpenAI=create, AuthenticationError=Exception,
        RateLimitError=Exception, NotFoundError=Exception))
    assert ask.ask_llm('question', provider, {'providers': {provider: {'api_key': 'synthetic'}}}).error == 'provider_error'


def test_unknown_provider_and_missing_dependency(monkeypatch):
    assert ask.ask_llm('question', 'unknown', {'providers': {}}).error == 'unknown_provider'
    monkeypatch.setitem(sys.modules, 'anthropic', None)
    assert ask.call_anthropic('question', {}, []).error == 'dependency_missing'


@pytest.mark.parametrize('provider', ['claude-code', 'notebooklm', 'anthropic', 'openai'])
def test_successful_adapters_preserve_answer(monkeypatch, provider):
    monkeypatch.setattr(ask.subprocess, 'run', lambda *a, **k: SimpleNamespace(
        returncode=0, stdout='a complete answer.' if provider == 'claude-code' else
        json.dumps({'value': {'answer': 'a complete answer. [1]'}})))
    response = SimpleNamespace(content=[SimpleNamespace(text='a complete answer.')],
        stop_reason='end_turn', choices=[SimpleNamespace(
            message=SimpleNamespace(content='a complete answer.'), finish_reason='stop')])
    client = SimpleNamespace(messages=SimpleNamespace(create=lambda **k: response),
        chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **k: response)))
    monkeypatch.setitem(sys.modules, 'anthropic', SimpleNamespace(Anthropic=lambda **k: client))
    monkeypatch.setitem(sys.modules, 'openai', SimpleNamespace(OpenAI=lambda **k: client,
        AuthenticationError=Exception, RateLimitError=Exception, NotFoundError=Exception))
    result = ask.ask_llm('question', provider, {'providers': {provider: {'api_key': 'synthetic'}}})
    assert result.ok and 'a complete answer.' in result.content
    assert result.error is None and not result.retryable


def stub_interface(monkeypatch, tmp_path, route):
    """No real configuration, notes, search, capture process, or model access."""
    monkeypatch.setattr(ask, 'NOTES_DIR', tmp_path)
    monkeypatch.setattr(ask, 'KnowledgeStore', lambda *a: SimpleNamespace(
        retrieve=lambda *a, **k: {'context': '', 'pages': []}))
    monkeypatch.setattr(ask, 'load_config', lambda: {'providers': {}, 'default_provider': 'codex'})
    monkeypatch.setattr(ask, 'load_index', lambda: ([], None))
    monkeypatch.setattr(ask, 'search', lambda *a: (
        [{'source': 'official', 'text': 'evidence'}] if route == 'official' else [], ['email']))
    monkeypatch.setattr(ask, 'cross_module_enrich', lambda *a: [])
    monkeypatch.setattr(ask, 'web_search_sap', lambda *a:
                        [{'title': 'synthetic', 'href': 'https://example.invalid', 'body': 'evidence'}]
                        if route == 'web' else [])
    monkeypatch.setattr(ask, '_cat', SimpleNamespace(start=lambda *a: None,
                        stop=lambda: None, set_msg=lambda *a: None))
    events = []
    monkeypatch.setattr(ask, 'display_answer', lambda answer: events.append(('display', answer)))
    monkeypatch.setattr(ask, '_save_note_safe', lambda *a, **k: events.append(('save', a[2])))
    monkeypatch.setattr(ask, 'remember_sap_capture', lambda q, a, s: events.append(('capture', a)))
    return events


@pytest.mark.parametrize('route', ['official', 'web', 'training'])
@pytest.mark.parametrize('ok', [False, True])
def test_cli_gates_all_answer_routes(tmp_path, monkeypatch, capsys, route, ok):
    events = stub_interface(monkeypatch, tmp_path, route)
    monkeypatch.setattr(ask, 'ask_llm', lambda *a, **k:
                        Result(True, content='valid answer') if ok else failure('timeout', True))
    monkeypatch.setattr(sys, 'argv', ['ask.py', '--provider', 'codex', 'synthetic question'])
    if ok:
        ask.main()
        assert events == [('display', 'valid answer'), ('save', 'valid answer'), ('capture', 'valid answer')]
    else:
        with pytest.raises(SystemExit) as exit_code:
            ask.main()
        assert exit_code.value.code == 1
        assert events == [] and list(tmp_path.iterdir()) == []
        output = capsys.readouterr()
        assert '回答失败' in output.out and 'stage=answer error=timeout' in output.err


@pytest.mark.parametrize('route', ['official', 'web', 'training'])
def test_tui_failure_never_enters_history_save_or_capture(tmp_path, monkeypatch, capsys, route):
    import prompt_toolkit
    import prompt_toolkit.history
    events = stub_interface(monkeypatch, tmp_path, route)
    prompts = iter(['first', 'failed', 'third', '/exit'])
    monkeypatch.setattr(prompt_toolkit, 'PromptSession', lambda **k:
                        SimpleNamespace(prompt=lambda *a: next(prompts)))
    monkeypatch.setattr(prompt_toolkit.history, 'FileHistory', lambda *a: None)
    histories = []

    def answer(prompt, provider, config, history):
        histories.append(list(history))
        return failure('process_error', True) if len(histories) == 2 else Result(True, content='valid answer')

    monkeypatch.setattr(ask, 'ask_llm', answer)
    ask.run_tui([], None, 'codex', {'providers': {}})
    assert histories == [[], [{'role': 'user', 'content': 'first'}, {'role': 'assistant', 'content': 'valid answer'}],
                         [{'role': 'user', 'content': 'first'}, {'role': 'assistant', 'content': 'valid answer'}]]
    assert events == [('display', 'valid answer'), ('save', 'valid answer'), ('capture', 'valid answer')] * 2
    assert list(tmp_path.iterdir()) == []
    assert 'stage=answer error=process_error' in capsys.readouterr().err


def test_failed_answer_is_rejected_before_raw_record_or_merge(tmp_path, monkeypatch):
    monkeypatch.setattr(ask, 'NOTES_DIR', tmp_path)
    monkeypatch.setattr(ask, '_ai_merge_note', lambda *a, **k: pytest.fail('merge must not run'))
    with pytest.raises(OperationFailed, match='timeout'):
        ask.save_note('question', failure('timeout'), [], ['email'], provider_name='codex', config={'providers': {}})
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize('outcome', ['model', 'conversion', 'invalid_docx', 'replace', 'append'])
def test_note_failure_preserves_live_docx(tmp_path, monkeypatch, outcome):
    monkeypatch.setattr(ask, 'NOTES_DIR', tmp_path)
    note = tmp_path / 'email-trigger.docx'
    Document().save(note)
    original = note.read_bytes()
    monkeypatch.setattr(ask, '_match_existing_note', lambda *a: note)
    calls = []
    monkeypatch.setattr(ask, 'ask_llm', lambda *a, **k: failure('timeout', True)
                        if outcome == 'model' else Result(True, content='merged body'))

    def convert(*args, outputfile, **kwargs):
        target = Path(outputfile)
        calls.append(target)
        assert target != note and target.parent.parent == tmp_path
        target.write_bytes(b'partial conversion')
        if outcome == 'conversion':
            raise RuntimeError('PRIVATE conversion')
        if outcome == 'invalid_docx':
            return
        Document().save(target)

    monkeypatch.setitem(sys.modules, 'pypandoc', SimpleNamespace(convert_text=convert))
    if outcome == 'replace':
        monkeypatch.setattr(Path, 'replace', lambda *a: (_ for _ in ()).throw(PermissionError('PRIVATE')))
    if outcome == 'append':
        from docx.document import Document as Doc
        def fail_save(self, target):
            calls.append(Path(target))
            Path(target).write_bytes(b'partial append')
            raise OSError('PRIVATE')
        monkeypatch.setattr(Doc, 'save', fail_save)
    from zipfile import BadZipFile
    with pytest.raises((OperationFailed, RuntimeError, OSError, BadZipFile)):
        ask.save_note('email question', 'valid answer', [], ['email'],
                      provider_name='codex' if outcome != 'append' else None, config={'providers': {}})
    assert note.read_bytes() == original
    assert not list(tmp_path.glob('.note-*'))
    assert len(list((tmp_path / 'knowledge/raw/notes').glob('*.md'))) == 1
    assert not calls if outcome == 'model' else calls


def test_successful_note_merge_atomically_replaces_docx(tmp_path, monkeypatch):
    monkeypatch.setattr(ask, 'NOTES_DIR', tmp_path)
    note = tmp_path / 'email-trigger.docx'
    Document().save(note)
    monkeypatch.setattr(ask, '_match_existing_note', lambda *a: note)
    monkeypatch.setattr(ask, 'ask_llm', lambda *a, **k: Result(True, content='merged answer'))

    def convert(body, *args, outputfile, **kwargs):
        assert 'merged answer' in body
        doc = Document()
        doc.add_paragraph(body)
        doc.save(outputfile)

    monkeypatch.setitem(sys.modules, 'pypandoc', SimpleNamespace(convert_text=convert))
    assert ask.save_note('question', 'valid answer', [], ['email'],
                         provider_name='codex', config={'providers': {}}) == note
    assert 'merged answer' in '\n'.join(p.text for p in Document(note).paragraphs)
    assert not list(tmp_path.glob('.note-*'))


@pytest.mark.parametrize('data, stage, code', [
    ('PRIVATE malformed', 'input', 'JSONDecodeError'),
    ('[]', 'input', 'ValueError'),
    ('{"message": 1}', 'input', 'ValueError'),
    ('{"message": "SAP PRIVATE"}', 'index_load', 'JSONDecodeError'),
])
def test_hook_failures_are_quiet_compatible_and_diagnostic(tmp_path, monkeypatch, capsys, data, stage, code):
    monkeypatch.setattr(kb_hook, 'INDEX_DIR', tmp_path)
    (tmp_path / 'chunks.json').write_text('PRIVATE malformed')
    (tmp_path / 'bm25.pkl').write_bytes(b'synthetic')
    monkeypatch.setattr(sys, 'stdin', io.StringIO(data))
    with pytest.raises(SystemExit) as exit_code:
        kb_hook.main()
    assert exit_code.value.code == 0
    output = capsys.readouterr()
    assert output.out == '' and f'stage={stage} error={code}' in output.err
    assert 'PRIVATE' not in output.err
