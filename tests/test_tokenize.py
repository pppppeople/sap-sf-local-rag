from lib.sf_tokenize import tokenize_query


def test_sap_terms_keep_searchable_aliases():
    tokens = tokenize_query("Onboarding RBP target population workflow")

    assert "onboarding" in tokens
    assert "rbp" in tokens
    assert "workflow" in tokens
