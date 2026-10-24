import json

import pytest

from scribe_intel.retrieval import profiles as P

PINNED_EXISTING_HASHES = {
    "dense-only": "96bb43a1b30381e8d71d8c7f577663da44f9e8a7f6f47bdcdf8fa0393f70170b",
    "lexical-only": "4800e93dd887e5d8a37be200dd29183c412231b876ca340618f5e9a47ebb9ece",
    "hybrid-rrf": "645b00f134edb5e06523f1f767e5dd82818870323f2f9d1364b6e61367eeecca",
    "dense-rerank": "ba3010453440ce650047a11a5309f229c9be68602649254b097cf572b33de0ea",
    "hybrid-rrf-rerank": "0117d32b812ec48088c8e0026523a05e7a08fb713853e3eace4f554d67050d12",
    "lexical-only-v2": "7cb423b2014e9048d64548b77ff7670581ae8f50d64e63267ca06f3945518ef8",
    "hybrid-rrf-v2": "b187bd6c2bdde832a1ced1055efbdaca53c7b65b3a5fe8b8f7734b92c54851aa",
    "hybrid-rrf-rerank-v2": "9c7199808e1aabfc2ee90a7e49583614ab6295fb684401d76357b1178ac2c21d",
    "hybrid-rrf-rerank-flat": "ceefdd729b8510216a40bef6ad4027ac68f04bdff4312d099225e1979d79f315",
    "hybrid-rrf-rerank-flat-v2": "bf6431f8ad574d5c82e34eb5ed2c83542fd051ed1bff174f41267643d171e138",
}


def test_five_profiles_unique_names_and_default():
    assert [p.name for p in P.SEED_PROFILES] == ["dense-only", "lexical-only", "hybrid-rrf", "dense-rerank", "hybrid-rrf-rerank"]
    assert P.DEFAULT_PROFILE.config.rerank and P.DEFAULT_PROFILE.config.budget_tokens == 6000
    assert len({p.config_hash for p in P.SEED_PROFILES}) == 5


def test_context_profiles_are_named_and_have_distinct_config_hashes():
    assert [p.config.context_strategy for p in P.CONTEXT_PROFILES] == [
        "top20_fused", "top5_reranked", "top5_parent", "compressed",
    ]
    assert all(p.config_json["context_strategy"] == p.config.context_strategy for p in P.CONTEXT_PROFILES)
    assert len({p.config_hash for p in P.CONTEXT_PROFILES}) == 4


def test_hash_stable_and_golden_for_canonical_json():
    cfg = P.PROFILE_A.config_json
    assert P.canonical_json(cfg) == json.dumps(dict(sorted(cfg.items())), separators=(",", ":"))
    assert P.hash_config(dict(reversed(list(cfg.items())))) == P.PROFILE_A.config_hash  # key order irrelevant
    assert P.hash_config({**cfg, "dense_top_k": 41}) != P.PROFILE_A.config_hash
    # frozen golden values: changing a profile must be a conscious new-profile decision
    assert P.PROFILE_E.config_hash == P.RetrievalProfile(**P.PROFILE_E.model_dump()).config_hash


def test_context_strategy_default_is_omitted_and_all_old_hashes_stay_pinned():
    assert "context_strategy" not in P.PROFILE_E.config_json
    assert P.ProfileConfig(dense_top_k=40).context_strategy == "legacy"
    assert {p.name: p.config_hash for p in P.SEED_PROFILES + P.V2_PROFILES} == PINNED_EXISTING_HASHES


def test_frozen():
    with pytest.raises(Exception):
        P.PROFILE_A.config.dense_top_k = 5  # type: ignore[misc]


def test_json_serialisable_for_jsonb_column():
    for p in P.ALL_PROFILES:
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
    assert len(c.rows) == len(P.ALL_PROFILES) == 14
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
    assert len(sql) == 14 and sql[0][1][0].endswith("1") and sql[4][1][0].endswith("5")
    assert [params[0] for _, params in sql[-4:]] == [P.SEED_IDS[p.name] for p in P.CONTEXT_PROFILES]
