from validator.checks.p4_consolidation import check_decision_artifact_provenance_contract


def test_decision_artifact_provenance_contract():
    result = check_decision_artifact_provenance_contract({})
    assert result["passed"] is True
    assert result["actual"]["missing_distill"] == []
    assert result["actual"]["missing_graph"] == []
    assert result["actual"]["missing_ui"] == []
