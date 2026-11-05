import json

import pytest
from docx import Document

from knowledge import KnowledgeStore, combine_context, enrich_payload, read_page


def make_note(path, paragraphs):
    doc = Document()
    for text in paragraphs:
        doc.add_paragraph(text)
    doc.save(path)


def test_bootstrap_preserves_original_table_order_and_live_index(tmp_path):
    note = tmp_path / 'email-trigger.docx'
    doc = Document()
    doc.add_paragraph('邮件未触发，尚未解决')
    table = doc.add_table(rows=1, cols=2)
    table.cell(0, 0).text = '实际状态'
    table.cell(0, 1).text = 'In Progress'
    doc.add_paragraph('表格之后的记录')
    doc.save(note)
    original = note.read_bytes()
    index = tmp_path / 'INDEX.md'
    index.write_text('已有索引')
    store = KnowledgeStore(tmp_path)
    assert store.bootstrap(tmp_path)['imported'] == 1
    frozen = store.root / 'raw/notes/legacy/email-trigger.docx'
    extracted = store.root / 'raw/notes/legacy/email-trigger.md'
    assert frozen.read_bytes() == original
    text = extracted.read_text()
    assert text.index('邮件未触发') < text.index('实际状态') < text.index('表格之后')
    make_note(note, ['后来补充的内容'])
    assert store.bootstrap(tmp_path)['imported'] == 0
    assert frozen.read_bytes() == original and extracted.read_text() == text
    assert index.read_text() == '已有索引'


def test_new_records_are_complete_and_do_not_upgrade_stale_pages(tmp_path):
    from knowledge import frontmatter
    store = KnowledgeStore(tmp_path)
    store.seed()
    page = store.root / 'wiki/concepts/邮件触发.md'
    meta, body = read_page(page)
    meta['stale'] = True
    page.write_text(frontmatter(meta, body))
    question = '邮件触发问题：' + '原始上下文' * 2000
    first = store.record(question, '没有结论', ['Onboarding / Onboarding_2.0'])
    original = first.read_bytes()
    second = store.record('邮件触发进一步观察', '仍未核验', [])
    assert first != second and first.read_bytes() == original
    assert question in first.read_text()
    meta, _ = read_page(page)
    assert meta['stale'] is True and meta['evidence_status'] == 'historical-unverified'
    assert len(meta['sources']) == 2
    assert all(p['title'] != '邮件触发' for p in store.retrieve('邮件触发')['pages'])


def test_query_reads_retained_case_and_keeps_official_citations_separate(tmp_path):
    store = KnowledgeStore(tmp_path)
    store.record('Custom Task 默认值', '租户观察：新建实例值为空，尚未解决', [])
    result = store.retrieve('custom task的picklist默认值如何配置')
    assert result['pages'][0]['title'] == '自定义任务默认值'
    assert '新建实例值为空' in result['context'] and '不是 SAP 官方事实' in result['context']
    sources = [{'source': 'Onboarding / Onboarding_2.0', 'documentVersion': '1H 2605'}]
    payload = enrich_payload({'formatted': '官方摘录', 'context': '官方摘录', 'sources': sources}, result)
    assert payload['sources'] == sources and payload['context'] == '官方摘录'
    assert payload['knowledgeSources'][0]['kind'] == 'private_wiki'
    assert '回查官方原文摘录' in payload['formatted']
    assert len(combine_context('X' * 20000, result)) < 14000
    assert not store.retrieve('Learning邮件模板配置')['pages']


def test_version_change_marks_only_dependencies_without_rebinding_old_evidence(tmp_path):
    from knowledge import OFFICIAL
    store = KnowledgeStore(tmp_path)
    store.seed()
    manifest = tmp_path / 'manifest.json'
    docs = [{'source': s, 'documentVersion': '1H 2605', 'publishedDate': '2026-05-15', 'status': 'active'}
            for s in sorted({s for refs in OFFICIAL.values() for s in refs})]
    manifest.write_text(json.dumps({'documents': docs}))
    store.check_sources(manifest, bind=True)
    for doc in docs:
        if doc['source'] == 'Platform / SF_IMP_MDF':
            doc['documentVersion'] = '2H 2026'
    manifest.write_text(json.dumps({'documents': docs}))
    changed = store.check_sources(manifest)
    assert '自定义任务默认值' in changed and '邮件触发' not in changed
    meta, _ = read_page(store.root / 'wiki/concepts/自定义任务默认值.md')
    assert meta['stale'] is True
    assert any(d['documentVersion'] == '1H 2605' for d in meta['official_dependencies'])
    store.check_sources(manifest, bind=True)
    assert read_page(store.root / 'wiki/concepts/自定义任务默认值.md')[0]['stale'] is True
    assert all(p['title'] != '自定义任务默认值' for p in store.retrieve('默认值')['pages'])


def test_ai_merge_failure_keeps_raw_input_and_previous_docx(tmp_path, monkeypatch):
    import ask
    monkeypatch.setattr(ask, 'NOTES_DIR', tmp_path)
    note = tmp_path / 'email-trigger.docx'
    make_note(note, ['旧版本笔记，尚未解决'])
    original = note.read_bytes()
    monkeypatch.setattr(ask, '_match_existing_note', lambda tokens: note)
    def fail(*args, **kwargs):
        raise RuntimeError('model merge failed')
    monkeypatch.setattr(ask, '_ai_merge_note', fail)
    with pytest.raises(RuntimeError, match='model merge failed'):
        ask.save_note('邮件触发的新观察', '仍然未发送', ['official-source'], ['email'],
                      provider_name='codex', config={'provider': 'codex'})
    records = list((tmp_path / 'knowledge/raw/notes').glob('*.md'))
    assert len(records) == 1 and '仍然未发送' in records[0].read_text()
    assert next((tmp_path / 'knowledge/raw/notes').glob('*-before.docx')).read_bytes() == original
    assert note.read_bytes() == original


def test_daily_capture_preserves_same_slug_formal_note(tmp_path, monkeypatch):
    import sap_asset
    monkeypatch.setattr(sap_asset, 'NOTES_DIR', tmp_path)
    monkeypatch.setattr(sap_asset, 'DAILY_CAPTURE_DIR', tmp_path / 'daily-capture')
    monkeypatch.setattr(sap_asset, 'update_index', lambda: None)
    formal = tmp_path / 'email.docx'
    formal.write_bytes(b'existing formal')
    def write(slug, title, body, *, record, directory):
        directory.mkdir(parents=True, exist_ok=True)
        result = directory / (slug + '.docx')
        result.write_bytes(b'new daily')
        return result
    monkeypatch.setattr(sap_asset, 'write_claude_note', write)
    first = sap_asset.capture('邮件', daily=True)
    second = sap_asset.capture('邮件', daily=True)
    assert formal.read_bytes() == b'existing formal'
    assert first.parent == second.parent == tmp_path / 'daily-capture'
    assert first != second and first.exists() and second.exists()
    assert len(list((tmp_path / 'knowledge/raw/notes').glob('*.md'))) == 2


def test_conversion_failure_still_retains_complete_markdown(tmp_path, monkeypatch):
    import ask
    import types
    import sys
    monkeypatch.setattr(ask, 'NOTES_DIR', tmp_path)
    def fail(*args, **kwargs):
        raise RuntimeError('conversion failed')
    monkeypatch.setitem(sys.modules, 'pypandoc', types.SimpleNamespace(convert_text=fail))
    with pytest.raises(RuntimeError, match='conversion failed'):
        ask.write_claude_note('custom-task', '默认值', '完整输入\n\n尚未核验')
    records = list((tmp_path / 'knowledge/raw/notes').glob('*.md'))
    assert len(records) == 1 and '完整输入\n\n尚未核验' in records[0].read_text()


def test_standard_yaml_can_be_edited_and_checked_by_another_assistant(tmp_path):
    store = KnowledgeStore(tmp_path)
    store.seed()
    page = store.root / 'wiki/concepts/邮件触发.md'
    original = page.read_text()
    page.write_text(original.replace('sources: []', 'sources:\n  - raw/notes/missing.md'))
    assert read_page(page)[0]['sources'] == ['raw/notes/missing.md']
    assert any('missing source' in e for e in store.validate())
    page.write_text(original)
    assert store.validate() == []


def test_visibility_query_does_not_pull_defaults_from_generic_task_overlap(tmp_path):
    store = KnowledgeStore(tmp_path)
    store.seed()
    titles = [p['title'] for p in store.retrieve('onboarding dashboard assigned to others 任务可见性')['pages']]
    assert titles == ['任务可见性']


def test_latest_cases_are_read_even_after_more_than_three_records(tmp_path):
    store = KnowledgeStore(tmp_path)
    for i in range(5):
        store.record('邮件触发案例', f'第{i}次观察', [])
    context = store.retrieve('邮件触发')['context']
    assert '第4次观察' in context and '第3次观察' in context


def test_private_asset_uses_local_index_without_external_processes(tmp_path, monkeypatch):
    import sap_asset
    notes = tmp_path / 'private-notes'
    KnowledgeStore(notes).record('邮件触发', 'FICTIONAL_PRIVATE_CASE_MARKER', [])
    monkeypatch.setattr(sap_asset, 'NOTES_DIR', notes)
    monkeypatch.setattr(sap_asset, 'PROJECT_DIR', tmp_path)
    monkeypatch.setattr(sap_asset, 'load_index', lambda: ([], None))
    queries = []
    def search(query, *_):
        queries.append(query)
        return ([{'source': 'Example / Fictional guide', 'text': 'FICTIONAL_EVIDENCE'}], [])
    monkeypatch.setattr(sap_asset, 'search', search)
    monkeypatch.setattr(sap_asset, 'cross_module_enrich', lambda *_: [])
    def forbidden(*_, **__):
        raise AssertionError('External process must not be called')
    monkeypatch.setattr(sap_asset.subprocess, 'run', forbidden)
    payload = sap_asset.query_context_data('邮件触发')
    assert 'Trigger Email Notification' in queries[0]
    assert 'FICTIONAL_PRIVATE_CASE_MARKER' not in queries[0]
    assert 'FICTIONAL_EVIDENCE' in payload['context']
    assert 'FICTIONAL_PRIVATE_CASE_MARKER' in payload['formatted']
    assert payload['knowledgeSources'][0]['kind'] == 'private_wiki'


def test_old_onboarding_does_not_reuse_current_starter_topics(tmp_path):
    store = KnowledgeStore(tmp_path)
    store.seed()
    assert not store.retrieve('Onboarding 1.0 邮件触发')['pages']
    assert store.match_topics('Onboarding 1.0 邮件触发') == []


def test_manual_consolidation_retains_each_original_before_removing_source_files(tmp_path, monkeypatch):
    import ask
    monkeypatch.setattr(ask, 'NOTES_DIR', tmp_path)
    first = tmp_path / 'email-trigger-first.docx'
    second = tmp_path / 'email-trigger-second.docx'
    make_note(first, ['第一次记录', '尚未解决'])
    make_note(second, ['第二次记录', '仍需验证'])
    originals = {first.read_bytes(), second.read_bytes()}
    monkeypatch.setattr('builtins.input', lambda prompt: 'y')
    ask.consolidate_notes()
    snapshots = {p.read_bytes() for p in (tmp_path / 'knowledge/raw/notes').glob('*-before.docx')}
    assert snapshots == originals
    text = '\n'.join(p.read_text() for p in (tmp_path / 'knowledge/raw/notes').glob('*.md'))
    assert '尚未解决' in text and '仍需验证' in text


def test_json_record_entrypoint_keeps_final_answer_and_model_review_status(tmp_path):
    import subprocess, sys, os
    from pathlib import Path
    env = {**os.environ, 'SAP_SF_NOTES_DIR': str(tmp_path)}
    entry = {'question': '原始邮件触发问题', 'answer': '最终回答，仍需租户验证',
             'sources': [{'source': 'Onboarding / Onboarding_2.0', 'documentVersion': '1H 2605'}],
             'modelReviewed': True}
    result = subprocess.run([sys.executable, 'sap_asset.py', 'knowledge-record'],
        cwd=Path(__file__).parents[1], env=env, input=json.dumps(entry, ensure_ascii=False),
        capture_output=True, text=True, check=True)
    path = tmp_path / json.loads(result.stdout)['record']
    meta, body = read_page(path)
    assert meta['evidence_status'] == 'model-reviewed'
    assert meta['verification'] == '未进行租户验证'
    assert entry['question'] in body and entry['answer'] in body
    assert '1H 2605' in body
    topic, _ = read_page(tmp_path / 'knowledge/wiki/concepts/邮件触发.md')
    assert topic['evidence_status'] == 'historical-unverified'
