import etf_lite.build as build


def test_events_cover_additions_and_removals_once():
    payload = build.build_site([])
    ev = payload["events"]["items"]
    assert payload["events"]["lookback_days"] == build.EVENT_LOOKBACK_DAYS
    types = {e["delta_type"] for e in ev}
    assert types <= {"addition", "removal"}
    keys = [(e["etf_ticker"], e["delta_type"], e["isin"] or e["constituent_ticker"],
             str(e["curr_as_of_date"])) for e in ev]
    assert len(keys) == len(set(keys)), "an event was emitted twice"
    assert all(e["curr_as_of_date"] for e in ev), "removals must carry the pair's end date"
    dates = [str(e["curr_as_of_date"]) for e in ev]
    assert dates == sorted(dates, reverse=True)
