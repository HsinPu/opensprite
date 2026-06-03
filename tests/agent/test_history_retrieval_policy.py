from opensprite.agent.history_retrieval_policy import (
    history_retrieval_metadata_has_results,
    history_retrieval_metadata_reports_empty,
)


def test_history_retrieval_metadata_reports_explicit_empty_counts():
    assert history_retrieval_metadata_reports_empty({"result_count": 0})
    assert history_retrieval_metadata_reports_empty({"hit_count": "0"})
    assert history_retrieval_metadata_reports_empty({"hits": []})


def test_history_retrieval_metadata_does_not_report_empty_when_hits_exist():
    assert not history_retrieval_metadata_reports_empty({"result_count": 2})
    assert not history_retrieval_metadata_reports_empty({"hits": [{"content": "prior note"}]})


def test_history_retrieval_metadata_reports_hits_separately_from_empty_counts():
    assert history_retrieval_metadata_has_results({"result_count": 2})
    assert history_retrieval_metadata_has_results({"hits": [{"content": "prior note"}]})
    assert not history_retrieval_metadata_has_results({"result_count": 0})


def test_history_retrieval_metadata_requires_explicit_count_field():
    assert not history_retrieval_metadata_reports_empty({})
    assert not history_retrieval_metadata_reports_empty(None)
