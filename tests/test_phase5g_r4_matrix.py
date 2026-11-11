from alpha_market_engine.production_certification import CertificationEvidence, CertificationMatrix, CertificationResult, REQUIRED_CERTIFICATION_SCENARIOS


def evidence(result=CertificationResult.PASS, executed=True):
    return [CertificationEvidence(s.scenario_id, executed, result, "test", "observed") for s in REQUIRED_CERTIFICATION_SCENARIOS]


def test_r4_matrix_all_pass_is_complete_and_first_post_is_separate():
    matrix = CertificationMatrix(evidence())
    assert matrix.implementation_complete()
    assert matrix.limited_auto_ready(first_real_post_certified=False)
    assert not matrix.limited_auto_active(first_real_post_certified=False)


def test_r4_matrix_one_fail_is_incomplete():
    items = evidence()
    items[0] = CertificationEvidence(items[0].scenario_id, True, CertificationResult.FAIL, "test", "failure")
    assert not CertificationMatrix(items).implementation_complete()


def test_r4_matrix_one_not_executed_is_incomplete():
    items = evidence()
    items[0] = CertificationEvidence(items[0].scenario_id, False, CertificationResult.NOT_EXECUTED, "test", "not run")
    assert not CertificationMatrix(items).implementation_complete()


def test_r4_matrix_requires_every_scenario():
    items = evidence()[:-1]
    assert not CertificationMatrix(items).implementation_complete()
