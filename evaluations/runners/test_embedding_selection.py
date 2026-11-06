"""Offline checks only. No credentials, model calls, database, server or workers."""
import copy
import importlib.util
import json
import math
from pathlib import Path
from unittest.mock import patch

import pytest

spec = importlib.util.spec_from_file_location("embedding_selection", Path(__file__).with_name("embedding_selection.py"))
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


def test_dataset_split_candidate_pool_and_request_envelope():
    data, config = runner.load()
    plan = runner.schedule(data)
    assert len(plan) == 40
    assert len(plan)+config["max_retries_global"] == 50
    assert all(len(rows) <= 10 for _, _, rows in plan)
    for provider in ("openai", "gemini"):
        queries = [r["id"] for p, stage, rows in plan if p == provider and stage == "query" for r in rows]
        assert len(set(queries)) == len(queries) == 48


def test_offline_prepare_does_not_import_network_or_credentials(tmp_path):
    import builtins
    real_import = builtins.__import__
    def safe_import(name, *args, **kwargs):
        assert name not in {"httpx", "dotenv"}
        return real_import(name, *args, **kwargs)
    with patch.object(runner, "OUT", tmp_path), patch("builtins.__import__", side_effect=safe_import):
        manifest = runner.prepare()
    assert manifest["status"] == "PREPARED_OFFLINE_NOT_AUTHORIZED"
    assert manifest["cost_reservation_usd"] < 0.10
    assert (tmp_path/"lexical_baseline.json").is_file()


def test_wrong_manifest_stops_before_env_import(tmp_path):
    with patch.object(runner, "OUT", tmp_path):
        runner.prepare()
        with pytest.raises(runner.SafeFailure, match="manifest_mismatch"):
            runner.live("not-approved", 0.10)


@pytest.mark.parametrize("cap", [0, -1, float("nan"), float("inf"), 1])
def test_invalid_budget_stops_before_credentials(tmp_path, cap):
    with patch.object(runner, "OUT", tmp_path):
        manifest = runner.prepare()
        with pytest.raises(runner.SafeFailure, match="invalid_approved_cap"):
            runner.live(runner.digest(manifest), cap)


def test_normalization_rejects_nonfinite_zero_and_wrong_dimensions():
    for vector in ([0.0]*1536, [math.nan]*1536, [math.inf]*1536, [True]*1536, [1.0]):
        with pytest.raises(runner.SafeFailure):
            runner.normalize(vector, 1536)
    v = runner.normalize([1.0]*1536, 1536)
    assert sum(x*x for x in v) == pytest.approx(1)


def test_openai_response_order_is_verified_not_assumed():
    response = {"data": [{"index": 1, "embedding": [2.0]*1536}, {"index": 0, "embedding": [1.0]*1536}]}
    assert len(runner.parse_vectors("openai", response, 2)) == 2
    response["data"][1]["index"] = 1
    with pytest.raises(runner.SafeFailure, match="invalid_response_order"):
        runner.parse_vectors("openai", response, 2)


def test_gemini_task_type_and_batch_count():
    data, _ = runner.load()
    body = runner.payload("gemini", "gemini-embedding-001", data["chunks"][:2], True)
    assert all(r["taskType"] == "RETRIEVAL_DOCUMENT" for r in body["requests"])
    assert runner.payload("gemini", "gemini-embedding-001", data["queries"][:1], False)["requests"][0]["taskType"] == "RETRIEVAL_QUERY"
    with pytest.raises(runner.SafeFailure, match="invalid_response_count"):
        runner.parse_vectors("gemini", {"embeddings": []}, 2)


def test_metric_denominator_and_incomplete_corpus_fail_closed():
    data, _ = runner.load()
    result = runner.evaluate(data, {})
    assert result["groups"]["holdout/all"]["n"] == 16
    assert result["groups"]["holdout/all"]["hit_count"] == 0
    assert result["groups"]["holdout/all"]["successful_request_hit5"] is None
    q = data["queries"][0]
    vectors = {q["id"]: [1.0], q["relevant_ids"][0]: [1.0]}
    assert runner.evaluate(data, vectors)["rows"][0]["hit5"] == 0


def test_threshold_uses_calibration_and_ranking_ties_use_id():
    rows = [{"top_score": .8, "answerable": True}, {"top_score": .2, "answerable": False}]
    assert runner.calibrate(rows) == .8
    data, _ = runner.load()
    q = copy.deepcopy(data["queries"][0])
    q["text"] = "zzzzzz"
    ranked = runner.rank(q, data["chunks"])
    assert [i for i, _ in ranked] == sorted(q["candidate_ids"])


@pytest.mark.parametrize("status,expected_attempts", [(401, 2), (429, 2), (500, 4)])
def test_live_failures_are_sanitized_and_reservation_precedes_request(tmp_path, status, expected_attempts):
    import httpx
    sentinel = "NEVER_PRINT_OR_PERSIST_TEST_SECRET"
    calls = []
    def post(self, url, **kwargs):
        files = list(tmp_path.glob("run-*/ledger.json"))
        assert len(files) == 1
        entries = json.loads(files[0].read_text())
        assert entries[-1]["status"] == "reserved_unknown"
        calls.append(url)
        return httpx.Response(status, json={"error": {"message": sentinel}})
    with patch.object(runner, "OUT", tmp_path), patch.dict("os.environ", {}, clear=True), \
         patch("dotenv.dotenv_values", return_value={"EVAL_OPENAI_API_KEY": sentinel, "EVAL_GEMINI_API_KEY": sentinel}), \
         patch.object(httpx.Client, "post", post), patch.object(runner.time, "sleep"):
        manifest = runner.prepare()
        runner.live(runner.digest(manifest), 0.10)
    assert len(calls) == expected_attempts
    for path in tmp_path.rglob("*.json"):
        assert sentinel not in path.read_text(encoding="utf-8")
    summary = json.loads(next(tmp_path.glob("run-*/summary.json")).read_text())
    assert summary["status"] == "incomplete"
    assert summary["reserved_usd"] > 0


@pytest.mark.parametrize("fail_repeat", [False, True])
def test_live_mock_success_is_complete_but_does_not_declare_winner(tmp_path, fail_repeat):
    import httpx
    calls = []
    def post(self, url, **kwargs):
        body = kwargs["json"]
        calls.append(url)
        if fail_repeat and len(calls) >= 37 and "openai.com" in url:
            return httpx.Response(400)
        if "openai.com" in url:
            return httpx.Response(200, json={"model": "text-embedding-3-small", "usage": None,
                "data": [{"index": i, "embedding": [1.0]*1536} for i in range(len(body["input"]))]})
        return httpx.Response(200, json={"embeddings": [{"values": [1.0]*1536} for _ in body["requests"]]})
    with patch.object(runner, "OUT", tmp_path), patch.dict("os.environ", {}, clear=True), \
         patch("dotenv.dotenv_values", return_value={"EVAL_OPENAI_API_KEY": "fake", "EVAL_GEMINI_API_KEY": "fake"}), \
         patch.object(httpx.Client, "post", post):
        manifest = runner.prepare()
        runner.live(runner.digest(manifest), 0.10)
    assert len(calls) == (39 if fail_repeat else 40)
    summary = json.loads(next(tmp_path.glob("run-*/summary.json")).read_text())
    assert summary["status"] == ("incomplete" if fail_repeat else "complete_pending_review")
    assert summary["decision"] == "requires_review_no_automatic_winner"
    for provider in ("openai", "gemini"):
        result = json.loads(next(tmp_path.glob(f"run-*/{provider}_retrieval.json")).read_text())
        assert result["status"] == ("incomplete" if fail_repeat and provider == "openai" else "complete")
        assert result["latency"]["query"]["n"] == 12
        assert result["holdout_answerable_intent_families"] == 4


def test_boolean_openai_indices_are_not_valid_order_proof():
    with pytest.raises(runner.SafeFailure, match="invalid_response_order"):
        runner.parse_vectors("openai", {"data": [{"index": False, "embedding": [1.0]*1536}]}, 1)


def test_malformed_usage_is_sanitized_incomplete(tmp_path):
    import httpx
    def post(self, url, **kwargs):
        if "openai.com" in url:
            return httpx.Response(200, json={"model": "text-embedding-3-small", "usage": ["not-metadata"],
                "data": [{"index": 0, "embedding": [1.0]*1536}]})
        return httpx.Response(401)
    with patch.object(runner, "OUT", tmp_path), patch.dict("os.environ", {}, clear=True), \
         patch("dotenv.dotenv_values", return_value={"EVAL_OPENAI_API_KEY": "fake", "EVAL_GEMINI_API_KEY": "fake"}), \
         patch.object(httpx.Client, "post", post):
        manifest = runner.prepare()
        runner.live(runner.digest(manifest), .10)
    entries = json.loads(next(tmp_path.glob("run-*/ledger.json")).read_text())
    assert entries[0]["status"] == "invalid_usage_metadata"
    assert json.loads(next(tmp_path.glob("run-*/summary.json")).read_text())["status"] == "incomplete"


def test_timeout_reservations_and_global_retry_exhaustion_stop(tmp_path):
    import httpx
    count = 0
    def post(self, url, **kwargs):
        nonlocal count
        count += 1
        if count % 2:
            raise httpx.ReadTimeout("must-not-appear-in-artifact")
        body = kwargs["json"]
        if "openai.com" in url:
            return httpx.Response(200, json={"model": "text-embedding-3-small",
                "data": [{"index": i, "embedding": [1.0]*1536} for i in range(len(body["input"]))]})
        return httpx.Response(200, json={"embeddings": [{"values": [1.0]*1536} for _ in body["requests"]]})
    with patch.object(runner, "OUT", tmp_path), patch.dict("os.environ", {}, clear=True), \
         patch("dotenv.dotenv_values", return_value={"EVAL_OPENAI_API_KEY": "fake", "EVAL_GEMINI_API_KEY": "fake"}), \
         patch.object(httpx.Client, "post", post), patch.object(runner.time, "sleep"):
        manifest = runner.prepare()
        runner.live(runner.digest(manifest), .10)
    entries = json.loads(next(tmp_path.glob("run-*/ledger.json")).read_text())
    assert count == 21  # Ten retries, then the eleventh uncertain first attempt stops.
    assert all(e["reserved_usd"] > 0 for e in entries)
    assert sum(e["attempt"] == 2 for e in entries) == 10
    assert entries[-1]["status"] == "transport_error_unknown_charge"
    assert json.loads(next(tmp_path.glob("run-*/summary.json")).read_text())["status"] == "incomplete"
