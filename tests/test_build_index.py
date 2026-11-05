import importlib
from pathlib import Path

import pytest


@pytest.fixture
def index_builder(tmp_path, monkeypatch):
    # Import-time auto-detection must not inspect the real official document tree.
    internal_docs = Path(__file__).parents[1] / 'docs'
    original_exists = Path.exists

    def exists(path):
        return False if path == internal_docs else original_exists(path)

    monkeypatch.setattr(Path, 'exists', exists)
    module = importlib.import_module('build_index')
    monkeypatch.setattr(module, 'DOCS_DIR', tmp_path / 'docs')
    monkeypatch.setattr(module, 'INDEX_DIR', tmp_path / 'index')
    return module


@pytest.mark.parametrize('text, expected', [
    ('  SAP  SF\t\tGuide\nnext line  ', 'SAP SF Guide next line'),
    ('first\n\n\n\nsecond\nline', 'first\n\nsecond line'),
    ('\n\t  \n', ''),
    ('入职 权限\n\n邮件', '入职 权限\n\n邮件'),
])
def test_clean_text_normalizes_whitespace_and_preserves_paragraphs(index_builder, text, expected):
    assert index_builder.clean_text(text) == expected


@pytest.mark.parametrize('word_count, lengths', [
    (0, []),
    (29, []),
    (30, [30]),
    (600, [600, 100]),
    (1029, [600, 529]),
    (1030, [600, 530, 30]),
])
def test_chunk_text_keeps_minimum_sized_chunks_and_discards_short_tail(
    index_builder, word_count, lengths,
):
    words = [f'word{i}' for i in range(word_count)]

    chunks = index_builder.chunk_text(' '.join(words), 'Onboarding / Guide', 'Onboarding')

    assert [len(chunk['text'].split()) for chunk in chunks] == lengths
    assert [chunk['id'] for chunk in chunks] == [
        f'Onboarding / Guide::{i}' for i in range(len(lengths))
    ]
    assert all(chunk['source'] == 'Onboarding / Guide' for chunk in chunks)
    assert all(chunk['module'] == 'Onboarding' for chunk in chunks)


def test_chunk_text_preserves_order_overlap_and_metadata(index_builder):
    words = [f'word{i}' for i in range(650)]

    chunks = index_builder.chunk_text('\n\t'.join(words), 'Platform / Guide', 'Platform')

    assert chunks == [
        {'id': 'Platform / Guide::0', 'source': 'Platform / Guide', 'module': 'Platform',
         'text': ' '.join(words[:600])},
        {'id': 'Platform / Guide::1', 'source': 'Platform / Guide', 'module': 'Platform',
         'text': ' '.join(words[500:])},
    ]
    assert chunks[0]['text'].split()[-100:] == chunks[1]['text'].split()[:100]


def test_tokenize_lowercases_and_splits_whitespace(index_builder):
    assert index_builder.tokenize(' SAP\tRBP\n权限  Email-Trigger ') == [
        'sap', 'rbp', '权限', 'email-trigger',
    ]
    assert index_builder.tokenize(' \n\t') == []
