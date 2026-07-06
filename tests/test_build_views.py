"""Tests for the dual-view payload (aligned vs latest) produced by build_site.

Funds post holdings on different lags, so on any given day they sit on different
as-of dates. build_site ships two delta views:
  - "latest": each fund diffs its own two freshest snapshots (mixed windows).
  - "aligned": every fund pinned to a common reference date (aligned_date) so the
    cross-section is same-day apples-to-apples.

Deltas come from the committed snapshot CSVs (data/snapshots). build_site writes
site/data.json as a side effect — that's fine; we don't assert on the file.

Runnable two ways:
    pytest tests/test_build_views.py
    python -m tests.test_build_views     # plain-assert runner, no pytest needed
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import etf_lite.build as build  # noqa: E402


def test_payload_ships_both_views_with_aligned_date():
    payload = build.build_site([])
    assert "views" in payload
    assert "aligned" in payload["views"]
    assert "latest" in payload["views"]
    assert payload.get("default_view") == "aligned"
    assert payload.get("aligned_date") is not None


def test_aligned_changes_share_single_current_date():
    payload = build.build_site([])
    aligned = payload["views"]["aligned"]
    # curr_as_of_date rows carry date objects; aligned_date is an ISO string.
    currents = {str(c["curr_as_of_date"]) for c in aligned["changes"]}
    # Alignment pins every fund to the same reference date, so all 'change' rows
    # in the aligned view carry a single curr_as_of_date.
    assert len(currents) <= 1, f"aligned changes span multiple dates: {sorted(currents)}"
    if currents:
        assert next(iter(currents)) == payload["aligned_date"]


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = 0
    for fn in fns:
        try:
            fn()
            passed += 1
        except AssertionError as e:
            print(f"FAIL {fn.__name__}: {e}")
    print(f"{passed}/{len(fns)} tests passed")
    sys.exit(0 if passed == len(fns) else 1)
