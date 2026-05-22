from validator.checks.p1_index import (
    check_artifact_chunk_freshness_mismatch_contract,
    check_freshness_telemetry_contract,
)
from validator.checks.p2_grounding import (
    check_citation_hard_gate_contract,
    check_claim_grounding_contract,
    check_no_evidence_fail_closed_contract,
)
from validator.checks.p4_consolidation import check_decision_provenance_schema_contract
from validator.e2e_runner import lint_e2e_spec


def test_freshness_validator_contracts():
    assert check_freshness_telemetry_contract({})["passed"] is True
    assert check_artifact_chunk_freshness_mismatch_contract({})["passed"] is True


def test_grounding_validator_contracts():
    assert check_citation_hard_gate_contract({})["passed"] is True
    assert check_claim_grounding_contract({"threshold": 0.75})["passed"] is True
    assert check_no_evidence_fail_closed_contract({})["passed"] is True


def test_decision_provenance_schema_contract():
    assert check_decision_provenance_schema_contract({})["passed"] is True


def test_reliability_e2e_spec_lints():
    ok, errors = lint_e2e_spec()
    assert ok, errors
