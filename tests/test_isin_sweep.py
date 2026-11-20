from etf_lite.isin_resolver import IsinResolver
from scripts.isin_sweep import bbg_security, valid_isin


def test_isin_check_digit():
    assert valid_isin("CA13321L1085")      # Cameco
    assert valid_isin("US0378331005")      # Apple
    assert not valid_isin("CA13321L1084")
    assert not valid_isin("XX1234567890")
    assert not valid_isin(None)


def test_bbg_security_suffix():
    assert bbg_security("CCO CN") == "CCO CN Equity"
    assert bbg_security("300014 C2") == "300014 C2 Equity"
    assert bbg_security("DNN") == "DNN US Equity"
    assert bbg_security(None) is None


def test_override_base_ticker_dropped_when_ambiguous():
    r = IsinResolver([], {"by_ticker": {"RIO": "US7672041008", "RIO LN": "GB0007188757",
                                        "CCO CN": "CA13321L1085"}})
    assert r.resolve("RIO LN")[0] == "GB0007188757"
    assert r.resolve("RIO")[0] == "US7672041008"       # exact key still wins
    assert r.resolve("RIO AU")[0] is None              # bare root ambiguous -> no guess
    assert r.resolve("CCO US")[0] == "CA13321L1085"    # unambiguous root still resolves
