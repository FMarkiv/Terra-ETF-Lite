from datetime import date, datetime, timezone
from unittest import mock

from etf_lite.parsers import globalx


class _Resp:
    def __init__(self, status, text):
        self.status_code, self.text = status, text


class _Sess:
    def __init__(self, ok_ymd):
        self.ok_ymd, self.calls = ok_ymd, []

    def get(self, url, timeout=None):
        self.calls.append(url)
        if self.ok_ymd in url:
            return _Resp(200, "Global X Copper Miners ETF\nFund Holdings Data as of 11/18/2026\n")
        return _Resp(404, "<!DOCTYPE html><html></html>")


def test_probe_walks_back_to_latest_file_and_skips_weekends():
    sess = _Sess("20261113")  # Friday; today is the following Monday
    with mock.patch.object(globalx, "datetime") as dt:
        dt.now.return_value = datetime(2026, 11, 16, 21, tzinfo=timezone.utc)
        url = globalx._probe_dated_csv("https://www.globalxetfs.com/funds/copx", session=sess)
    assert url == "https://assets.globalxetfs.com/funds/holdings/copx_full-holdings_20261113.csv"
    assert not any(d in u for u in sess.calls for d in ("20261115", "20261114"))


def test_probe_returns_none_when_nothing_found():
    sess = _Sess("19000101")
    assert globalx._probe_dated_csv("https://www.globalxetfs.com/funds/copx", session=sess) is None
