"""Tests for detector.datasets: synthetic cresci-style nested zip and a tiny backend SQLite DB."""

from __future__ import annotations

import io
import sqlite3
import zipfile
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import pytest

from detector import datasets as ds


def _epoch(*args) -> float:
    return datetime(*args, tzinfo=timezone.utc).timestamp()


# ----------------------------------------------------------------------------- cresci fixture

USERS_HDR = "id,name,screen_name,statuses_count,followers_count,friends_count,created_at"
TWEETS_HDR = ('"id","text","user_id","in_reply_to_status_id","created_at","timestamp"')


def _nested_zip(files: dict) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, content in files.items():
            zf.writestr(name, content)
    return buf.getvalue()


@pytest.fixture
def cresci_zip(tmp_path):
    genuine_users = "\n".join([
        USERS_HDR,
        '"1001","Ann","ann",3,10,5,"Tue Mar 03 08:21:38 +0000 2009"',
        '"1002","Bob","bob",0,0,0,"Wed Oct 07 03:19:21 +0000 2009"',
    ])
    # 5 tweets for 1001 (2 would be dropped at cap 3); one for an unknown user 9999 (must be ignored).
    genuine_tweets = "\n".join([
        TWEETS_HDR + ',"ignored"',
        '"11","old one","1001","0","Mon Jan 02 10:00:00 +0000 2017","2017-01-02 12:00:00","x"',
        '"12","second","1001","0","Mon Jan 02 11:00:00 +0000 2017","2017-01-02 13:00:00","x"',
        '"13","third","1001","0","Mon Jan 02 12:00:00 +0000 2017","2017-01-02 14:00:00","x"',
        '"14","fourth","1001","0","Mon Jan 02 13:00:00 +0000 2017","2017-01-02 15:00:00","x"',
        '"15","fifth","1001","0","Mon Jan 02 14:00:00 +0000 2017","2017-01-02 16:00:00","x"',
        '"16","stranger","9999","0","Mon Jan 02 14:00:00 +0000 2017","2017-01-02 16:00:00","x"',
    ])
    spam_users = "\n".join([
        USERS_HDR,
        '"2001","Spam","spam1",500,2,900,"Fri May 01 00:18:11 +0000 2015"',
    ])
    spam_tweets = "\n".join([
        TWEETS_HDR + ',"ignored"',
        '"21","buy now http://t.co/abc","2001","0","Fri May 01 00:18:11 +0000 2015","x","y"',
        '"22","reply","2001","12345","Fri May 01 00:19:11 +0000 2015","x","y"',
        '"23","null reply","2001","NULL","Fri May 01 00:20:11 +0000 2015","x","y"',
    ])
    trad_users = "\n".join([
        USERS_HDR,
        '"3001","Trad","trad1",7,1,1,"Sat Feb 02 01:02:03 +0000 2013"',
    ])
    outer = {
        "datasets_full.csv/genuine_accounts.csv.zip": _nested_zip({
            "genuine_accounts.csv/users.csv": genuine_users,
            "genuine_accounts.csv/tweets.csv": genuine_tweets,
            "__MACOSX/genuine_accounts.csv/._users.csv": "junk",
        }),
        "datasets_full.csv/social_spambots_1.csv.zip": _nested_zip({
            "social_spambots_1.csv/users.csv": spam_users,
            "social_spambots_1.csv/tweets.csv": spam_tweets,
        }),
        # group with no tweets.csv at all
        "datasets_full.csv/traditional_spambots_2.csv.zip": _nested_zip({
            "traditional_spambots_2.csv/users.csv": trad_users,
        }),
        # not account data: must be skipped
        "datasets_full.csv/crowdflower_results.csv.zip": _nested_zip({
            "crowdflower_results.csv/crowdflower_results_aggregated.csv": "a,b\n1,2\n",
        }),
        "datasets_full.csv/READ.ME": "readme",
    }
    path = tmp_path / "cresci.csv.zip"
    with zipfile.ZipFile(path, "w") as zf:
        for name, data in outer.items():
            zf.writestr(name, data)
    return path


def test_cresci_families_labels_and_accounts(cresci_zip):
    accounts, posts = ds.load_cresci2017(cresci_zip)
    assert list(accounts.columns) == ds.ACCOUNT_COLUMNS
    assert list(posts.columns) == ds.POST_COLUMNS
    assert set(accounts["family"]) == {"genuine", "social_spambots_1", "traditional_spambots_2"}
    by_id = accounts.set_index("account_id")
    assert sorted(by_id.index) == ["1001", "1002", "2001", "3001"]
    assert by_id.loc["1001", "label"] == 0
    assert by_id.loc["2001", "label"] == 1
    assert by_id.loc["3001", "label"] == 1
    assert set(accounts["source"]) == {"cresci-2017"}
    assert by_id.loc["1001", "followers"] == 10
    assert by_id.loc["1001", "following"] == 5
    assert by_id.loc["1001", "posts_total"] == 3
    assert by_id.loc["1001", "created_ts"] == _epoch(2009, 3, 3, 8, 21, 38)


def test_cresci_post_cap_keeps_most_recent(cresci_zip):
    _, posts = ds.load_cresci2017(cresci_zip, max_posts_per_account=3)
    mine = posts[posts["account_id"] == "1001"].sort_values("ts")
    assert list(mine["post_id"]) == ["13", "14", "15"]
    assert mine["ts"].iloc[-1] == _epoch(2017, 1, 2, 14, 0, 0)  # created_at, not the naive timestamp column


def test_cresci_posts_fields_and_unknown_users(cresci_zip):
    _, posts = ds.load_cresci2017(cresci_zip)
    assert "9999" not in set(posts["account_id"])
    assert set(posts["thread_id"]) == {""}
    assert set(posts["urls"]) == {""}
    spam = posts[posts["account_id"] == "2001"].set_index("post_id")
    assert spam.loc["21", "parent_id"] == ""
    assert spam.loc["22", "parent_id"] == "12345"
    assert spam.loc["23", "parent_id"] == ""  # NULL is missing
    assert spam.loc["21", "ts"] == _epoch(2015, 5, 1, 0, 18, 11)
    assert spam.loc["21", "text"] == "buy now http://t.co/abc"


def test_cresci_group_without_tweets_keeps_accounts(cresci_zip):
    accounts, posts = ds.load_cresci2017(cresci_zip)
    trad = accounts[accounts["family"] == "traditional_spambots_2"]
    assert list(trad["account_id"]) == ["3001"]
    assert "3001" not in set(posts["account_id"])
    assert trad["created_ts"].iloc[0] == _epoch(2013, 2, 2, 1, 2, 3)


def test_cresci_seed_is_deterministic(cresci_zip):
    a1, p1 = ds.load_cresci2017(cresci_zip, max_posts_per_account=2, seed=7)
    a2, p2 = ds.load_cresci2017(cresci_zip, max_posts_per_account=2, seed=7)
    pd.testing.assert_frame_equal(p1, p2)
    pd.testing.assert_frame_equal(a1, a2)


# ----------------------------------------------------------------------------- backend fixture

COMMENTS_DDL = """
CREATE TABLE comments (
  id TEXT PRIMARY KEY, thread_id TEXT, platform TEXT, user TEXT, ts REAL, text TEXT, source_text TEXT,
  account TEXT, infra TEXT, simulated INTEGER DEFAULT 0, ingested_at REAL, url TEXT, normalized INTEGER, likes INTEGER
)"""
THREADS_DDL = """
CREATE TABLE threads (
  id TEXT PRIMARY KEY, platform TEXT, external_id TEXT, title TEXT, url TEXT,
  published REAL, simulated INTEGER DEFAULT 0, created_at REAL
)"""


@pytest.fixture
def backend_db(tmp_path):
    path = tmp_path / "backend.db"
    con = sqlite3.connect(path)
    con.execute(COMMENTS_DDL)
    con.execute(THREADS_DDL)
    rows = [
        # id, thread, platform, user, ts(ms), text, account, simulated
        ("c1", "t1", "youtube", "yt_a", 1_700_000_000_000.0,
         "see https://example.com/x, and (http://bit.ly/abc).",
         '{"created": "2019-05-01T12:00:00.123456Z", "followers": 12}', 0),
        ("c2", "t1", "youtube", "yt_a", 1_700_000_100_000.0, "hello", None, 0),
        ("c3", "t2", "youtube", "yt_b", 1_700_000_200.0,  # already seconds
         None, '{"created": 1500000000, "followers": null}', 0),
        ("c4", "t2", "youtube", "yt_sim", 1_700_000_300_000.0, "simulated", '{"followers": 1}', 1),
        ("c5", "t2", "youtube", "yt_c", 1_700_000_400_000.0, "bad json", "not json", 0),
    ]
    con.executemany(
        "INSERT INTO comments (id, thread_id, platform, user, ts, text, account, simulated) "
        "VALUES (?,?,?,?,?,?,?,?)", rows,
    )
    con.commit()
    con.close()
    return path


def test_backend_default_skips_simulated(backend_db):
    accounts, posts = ds.load_backend_db(backend_db)
    assert list(accounts.columns) == ds.ACCOUNT_COLUMNS
    assert sorted(accounts["account_id"]) == ["yt_a", "yt_b", "yt_c"]
    assert sorted(posts["post_id"]) == ["c1", "c2", "c3", "c5"]
    assert set(accounts["family"]) == {"youtube"}
    assert set(accounts["source"]) == {"backend"}
    assert accounts["label"].isna().all()
    assert accounts["following"].isna().all() and accounts["posts_total"].isna().all()


def test_backend_include_simulated(backend_db):
    accounts, posts = ds.load_backend_db(backend_db, include_simulated=True)
    assert "yt_sim" in set(accounts["account_id"])
    assert "c4" in set(posts["post_id"])


def test_backend_account_metadata_parsing(backend_db):
    accounts, _ = ds.load_backend_db(backend_db)
    by_id = accounts.set_index("account_id")
    assert by_id.loc["yt_a", "created_ts"] == pytest.approx(_epoch(2019, 5, 1, 12, 0, 0) + 0.123456)
    assert by_id.loc["yt_a", "followers"] == 12
    assert by_id.loc["yt_b", "created_ts"] == 1_500_000_000.0  # numeric created
    assert np.isnan(by_id.loc["yt_b", "followers"])
    assert np.isnan(by_id.loc["yt_c", "created_ts"])  # invalid JSON -> unknown


def test_backend_post_fields(backend_db):
    _, posts = ds.load_backend_db(backend_db)
    by_id = posts.set_index("post_id")
    assert by_id.loc["c1", "ts"] == 1_700_000_000.0  # ms converted to seconds
    assert by_id.loc["c3", "ts"] == 1_700_000_200.0  # seconds left alone
    assert by_id.loc["c1", "urls"] == "https://example.com/x http://bit.ly/abc"
    assert by_id.loc["c2", "urls"] == ""
    assert by_id.loc["c3", "text"] == ""  # NULL text -> ''
    assert set(posts["thread_id"]) == {"t1", "t2"}
    assert set(posts["parent_id"]) == {""}


def test_save_load_roundtrip(backend_db, tmp_path, monkeypatch):
    monkeypatch.setattr(ds, "PROCESSED_DIR", tmp_path / "processed")
    accounts, posts = ds.load_backend_db(backend_db)
    acc_path, post_path = ds.save("unit_test", accounts, posts)
    assert acc_path.name == "unit_test_accounts.csv.gz" and post_path.exists()
    acc2, post2 = ds.load("unit_test")
    pd.testing.assert_frame_equal(accounts, acc2, check_dtype=False)
    pd.testing.assert_frame_equal(posts, post2, check_dtype=False)


# ----------------------------------------------------------------------------- helpers

@pytest.mark.parametrize("raw, expected", [
    ("Tue Mar 03 08:21:38 +0000 2009", _epoch(2009, 3, 3, 8, 21, 38)),
    ("2019-05-01T12:00:00Z", _epoch(2019, 5, 1, 12, 0, 0)),
    ("2019-05-01 12:00:00", _epoch(2019, 5, 1, 12, 0, 0)),  # naive -> UTC
    ("garbage", np.nan),
    ("", np.nan),
])
def test_parse_time_string(raw, expected):
    got = ds._parse_time_string(raw)
    if np.isnan(expected):
        assert np.isnan(got)
    else:
        assert got == pytest.approx(expected)


def test_to_epoch_mixed_units():
    out = ds._to_epoch([1_700_000_000_000, 1_700_000_000, "2019-05-01T12:00:00Z", None])
    assert out[0] == 1_700_000_000.0
    assert out[1] == 1_700_000_000.0
    assert out[2] == _epoch(2019, 5, 1, 12, 0, 0)
    assert np.isnan(out[3])


def test_extract_urls():
    assert ds._extract_urls("a http://x.com/p?q=1. b https://y.org!") == "http://x.com/p?q=1 https://y.org"
    assert ds._extract_urls(None) == ""
    assert ds._extract_urls("no links") == ""
