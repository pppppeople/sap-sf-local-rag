import pytest
from docx import Document

import sap_asset


@pytest.mark.parametrize('text, expected', [
    ('入职 权限 工作流 邮件 字段 配置 流程',
     'onboarding-permission-workflow-email-field-configuration-process'),
    ('The RBP issue for a NEW-HIRE with EC 2', 'rbp-new-hire-ec'),
    ('one two three four five six seven eight', 'one-two-three-four-five-six-seven'),
    ('中文？！', 'sap-note'),
    ('', 'sap-note'),
])
def test_slugify_translates_filters_and_limits_words(text, expected):
    assert sap_asset.slugify(text) == expected


def test_slugify_uses_supplied_fallback():
    assert sap_asset.slugify('the and note', fallback='custom-capture') == 'custom-capture'


def test_category_uses_first_match_while_tags_keep_all_matches():
    text = 'Onboarding RBP workflow email not visible'

    assert sap_asset.category_for(text) == 'Onboarding'
    assert sap_asset.tags_for(text) == [
        'Onboarding', 'RBP / Permission', 'Workflow / Rule',
        'Email / Notification', 'Troubleshooting',
    ]


def test_category_and_tags_handle_unknown_text_and_case():
    assert sap_asset.category_for('普通记录') == 'General'
    assert sap_asset.tags_for('普通记录') == ['General']
    assert sap_asset.category_for('RBP PERMISSION') == 'RBP / Permission'
    assert sap_asset.tags_for('RBP PERMISSION') == ['RBP / Permission']


@pytest.mark.parametrize('text, expected', [
    ('RBP workflow error', 'troubleshooting'),
    ('RBP workflow', 'workflow-rule'),
    ('permission email', 'permission'),
    ('邮件 字段', 'notification'),
    ('MDF object', 'configuration'),
    ('普通记录', 'configuration'),
])
def test_case_type_respects_priority_and_default(text, expected):
    assert sap_asset.case_type_for(text) == expected


@pytest.mark.parametrize('text, expected', [
    ('普通记录', 'basic'),
    ('RBP', 'basic'),
    ('RBP integration', 'intermediate'),
    ('RBP integration approval', 'intermediate'),
    ('RBP integration approval external user', 'advanced'),
])
def test_difficulty_thresholds(text, expected):
    assert sap_asset.difficulty_for(text) == expected


def test_note_title_and_preview_skip_blank_paragraphs_and_title(tmp_path):
    path = tmp_path / 'email-case.docx'
    doc = Document()
    for text in ['', '  邮件触发  ', '', ' 第一条观察 ', '邮件触发', '第二条观察']:
        doc.add_paragraph(text)
    doc.save(path)

    assert sap_asset.note_title(path) == '邮件触发'
    assert sap_asset.note_preview(path) == '第一条观察 第二条观察'


def test_note_title_and_preview_limit_extracted_text(tmp_path):
    path = tmp_path / 'long-note.docx'
    doc = Document()
    doc.add_paragraph('标题' * 60)
    doc.save(path)
    assert sap_asset.note_title(path) == ('标题' * 60)[:90]

    doc = Document()
    doc.add_paragraph('标题')
    doc.add_paragraph('观' * 250)
    doc.add_paragraph('不应追加的后续段落')
    doc.save(path)
    assert sap_asset.note_preview(path) == '观' * 220


@pytest.mark.parametrize('valid_docx', [True, False])
def test_empty_or_invalid_docx_uses_filename_title(tmp_path, valid_docx):
    path = tmp_path / 'email-trigger.docx'
    if valid_docx:
        Document().save(path)
    else:
        path.write_bytes(b'not a docx')

    assert sap_asset.note_title(path) == 'Email Trigger'
    assert sap_asset.note_preview(path) == ''


def test_capture_markdown_uses_first_nonempty_line_and_retains_full_input():
    first_line = '入职 邮件 ' + '观察' * 30
    raw = '\n\n  ' + first_line + '\n第二行：尚未验证\n'

    slug, title, body = sap_asset.build_capture_markdown(raw)

    assert slug == 'onboarding-email'
    assert title == first_line[:42]
    assert raw.strip() in body
    assert '1. 现象或需求：' + first_line in body
    assert '## 待补充' in body


def test_empty_capture_markdown_has_default_title_and_slug():
    slug, title, body = sap_asset.build_capture_markdown(' \n\t')

    assert slug == 'sap-note'
    assert title == 'SAP SF Project Capture'
    assert '## 问题摘要' in body


def test_metadata_uses_relative_path_and_deduplicates_tags(tmp_path, monkeypatch):
    monkeypatch.setattr(sap_asset, 'NOTES_DIR', tmp_path)
    monkeypatch.setattr(sap_asset, 'METADATA_DIR', tmp_path / 'metadata')
    note = tmp_path / 'daily-capture/email-case.docx'
    note.parent.mkdir()
    Document().save(note)

    path = sap_asset.write_metadata(
        note, '邮件观察', 'Email / Notification', ['Email', 'Email', 'Review'], '尚未验证',
    )

    assert path == tmp_path / 'metadata/email-case.md'
    text = path.read_text(encoding='utf-8')
    assert '- file: `daily-capture/email-case.docx`' in text
    assert '- category: Email / Notification' in text
    assert '- tags: Email, Review\n' in text
    assert '- case_type: notification' in text
    assert '- difficulty: basic' in text
    assert '## Preview\n\n尚未验证' in text


def test_metadata_has_placeholder_when_preview_is_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(sap_asset, 'NOTES_DIR', tmp_path)
    monkeypatch.setattr(sap_asset, 'METADATA_DIR', tmp_path / 'metadata')
    note = tmp_path / 'sample.docx'
    Document().save(note)

    path = sap_asset.write_metadata(note, '普通记录', 'General', [], '')

    assert '## Preview\n\nNo preview extracted.' in path.read_text(encoding='utf-8')
