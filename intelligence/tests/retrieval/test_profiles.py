import json

import pytest

from scribe_intel.retrieval import profiles as P


def test_five_profiles_unique_names_and_default():
    assert [p.name for p in P.SEED_PROFILES] == ["dense-only", "lexical-only", "hybrid-rrf", "dense-rerank", "hybrid-rrf-rerank"]
    assert P.DEFAULT_PROFILE.config.rerank and P.DEFAULT_PROFILE.config.budget_tokens == 6000
    assert len({p.config_hash for p in P.SEED_PROFILES}) == 5


def test_hash_stable_and_golden_for_canonical_json():
    cfg = P.PROFILE_A.config_json
    assert P.canonical_json(cfg) == json.dumps(dict(sorted(cfg.items())), separators=(",", ":"))
    assert P.hash_config(dict(reversed(list(cfg.items())))) == P.PROFILE_A.config_hash  # key order irrelevant
    assert P.hash_config({**cfg, "dense_top_k": 41}) != P.PROFILE_A.config_hash
    # frozen golden values: changing a profile must be a conscious new-profile decision
    assert P.PROFILE_E.config_hash == P.RetrievalProfile(**P.PROFILE_E.model_dump()).config_hash


def test_frozen():
    with pytest.raises(Exception):
        P.PROFILE_A.config.dense_top_k = 5  # type: ignore[misc]


def test_json_serialisable_for_jsonb_column():
    for p in P.SEED_PROFILES:
        assert json.loads(json.dumps(p.config_json)) == p.config_json
        assert "name" not in p.config_json


class FakeConn:
    def __init__(self, tamper=None):
        self.rows, self.executed, self.tamper = {}, [], tamper

    def execute(self, sql, params=()):
        self.executed.append(sql)
        conn = self
        if sql.startswith("INSERT"):
            _id, name, cfg, h = params
            self.rows.setdefault(name, (json.loads(cfg), h))
            return self
        name = params[0]
        row = self.rows.get(name)
        if row and self.tamper:
            row = self.tamper(row)
        self._row = row
        return self

    def fetchone(self):
        return self._row


def test_upsert_idempotent_and_verifies():
    c = FakeConn()
    P.upsert_profiles(c)
    P.upsert_profiles(c)
    assert len(c.rows) == 5
    assert all("ON CONFLICT (name) DO NOTHING" in s for s in c.executed if s.startswith("INSERT"))


def test_upsert_detects_drift():
    c = FakeConn()
    P.upsert_profiles(c)
    c.rows["dense-only"] = ({**c.rows["dense-only"][0], "dense_top_k": 10}, c.rows["dense-only"][1])
    with pytest.raises(P.ProfileImmutableError):
        P.upsert_profiles(c)
    c2 = FakeConn()
    P.upsert_profiles(c2)
    c2.rows["hybrid-rrf"] = (c2.rows["hybrid-rrf"][0], "0" * 64)
    with pytest.raises(P.ProfileImmutableError):
        P.upsert_profiles(c2)


def test_seed_sql_params():
    sql = P.seed_sql()
    assert len(sql) == 5 and sql[0][1][0].endswith("1") and sql[4][1][0].endswith("5")
