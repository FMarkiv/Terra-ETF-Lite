from etf_lite.engine import reconcile_key_flips


def _row(t, etf, tk, name, isin, pw=None, cw=None, ps=None, cs=None):
    return {"delta_type": t, "etf_ticker": etf, "constituent_ticker": tk, "constituent_name": name,
            "isin": isin, "prev_weight_pct": pw, "curr_weight_pct": cw, "prev_shares": ps,
            "curr_shares": cs, "prev_market_value": None, "curr_market_value": None,
            "prev_as_of_date": "2026-11-04" if pw is not None else None,
            "curr_as_of_date": "2026-11-18" if cw is not None else None}


def test_isin_change_becomes_single_change():
    rows = [_row("removal", "4COP", "NGEX CN", "NGEX MINERALS LTD", "CA62930A1021", pw=1.17, ps=100.0),
            _row("addition", "4COP", "NGEX CN", "NGEX MINERALS LTD", "CA62930A2011", cw=1.075, cs=110.0)]
    out = reconcile_key_flips(rows)
    assert len(out) == 1 and out[0]["delta_type"] == "change"
    assert out[0]["isin"] == "CA62930A2011"
    assert abs(out[0]["delta_weight_pct"] - (1.075 - 1.17)) < 1e-9
    assert abs(out[0]["pct_change_shares"] - 10.0) < 1e-9


def test_isin_missing_one_day_matches_on_name():
    rows = [_row("removal", "SILJ", "USAS", "Americas Gold & Silver Corp", "CA03062D8035", pw=1.9, ps=1.0),
            _row("addition", "SILJ", "USAS US", "AMERICAS GOLD & SILVER CORP", None, cw=1.85, cs=1.0)]
    assert [r["delta_type"] for r in reconcile_key_flips(rows)] == ["change"]


def test_different_funds_and_different_names_untouched():
    rows = [_row("removal", "GDX", "ABC CN", "ALPHA CORP", "CA0000000001", pw=1.0),
            _row("addition", "GDXJ", "ABC CN", "ALPHA CORP", "CA0000000002", cw=1.0),
            _row("addition", "GDX", "XYZ CN", "OTHER CORP", "CA0000000003", cw=1.0)]
    assert sorted(r["delta_type"] for r in reconcile_key_flips(rows)) == ["addition", "addition", "removal"]
