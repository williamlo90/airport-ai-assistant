"""Bounded embedding selection spike. Default prepare is offline and never reads .env."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import statistics
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "evaluations/datasets/embedding_selection.json"
CONFIG = ROOT / "evaluations/rubrics/embedding_selection.json"
OUT = ROOT / "local/provider-selection"


class SafeFailure(Exception):
    """Only fixed diagnostics, never provider response bodies or credentials."""


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def load():
    if not __debug__:
        raise SafeFailure("optimized_python_not_supported")
    data = json.loads(DATA.read_text(encoding="utf-8"))
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    chunks, queries = data["chunks"], data["queries"]
    ids = {c["id"] for c in chunks}
    assert len(chunks) == len(ids) == 40
    assert len(queries) == len({q["id"] for q in queries}) == 48
    families = {}
    counts = Counter()
    for row in chunks + queries:
        assert 0 < len(row["text"].split()) <= 150
        # Corpus is short Latin text; reject oversized inputs before any request.
        assert len(row["text"].encode("utf-8")) <= 1024
    for q in queries:
        assert q["split"] in {"calibration", "holdout"}
        assert families.setdefault(q["family"], q["split"]) == q["split"]
        pool = [c["id"] for c in chunks if c["language"] == q["source_language"]]
        assert q["candidate_ids"] == pool
        assert set(q["relevant_ids"]) <= set(pool)
        assert bool(q["relevant_ids"]) == q["answerable"]
        counts[(q["direction"], q["split"], q["answerable"])] += 1
    for direction in ("en-en", "id-id", "en-id", "id-en"):
        assert counts[direction, "calibration", True] == 6
        assert counts[direction, "holdout", True] == 4
    for language in ("en", "id"):
        for split in ("calibration", "holdout"):
            assert sum(not q["answerable"] and q["language"] == language and q["split"] == split for q in queries) == 2
    assert set(data["repeat_query_ids"]) <= {q["id"] for q in queries}
    assert len(set(data["repeat_query_ids"])) == 8
    assert config["max_retries_global"] >= 10
    assert config["dimensions"] == 1536
    assert set(config["providers"]) == {"openai", "gemini"}
    for provider, model in (("openai", "text-embedding-3-small"), ("gemini", "gemini-embedding-001")):
        assert config["providers"][provider]["model"] == model
        price = config["providers"][provider]["usd_per_million_tokens"]
        assert isinstance(price, (int, float)) and math.isfinite(price) and price > 0
    return data, config


def schedule(data):
    """The same batches and alternating provider order are fixed before live calls."""
    chunks, queries = data["chunks"], data["queries"]
    repeat = [next(q for q in queries if q["id"] == i) for i in data["repeat_query_ids"]]
    groups = [("preflight-document", chunks[:1]), ("preflight-query", queries[:1])]
    groups += [("corpus", chunks[i:i+10]) for i in range(0, len(chunks), 10)]
    groups += [("query", queries[i:i+4]) for i in range(0, len(queries), 4)]
    groups += [("repeat", repeat[i:i+4]) for i in range(0, 8, 4)]
    return [(provider, stage, rows) for i, (stage, rows) in enumerate(groups)
            for provider in (("openai", "gemini") if i % 2 == 0 else ("gemini", "openai"))]


def tokens(text):
    return set(re.findall(r"[^\W_]+", text.lower(), flags=re.UNICODE))


def rank(q, chunks, vectors=None):
    scores = []
    for c in chunks:
        if c["id"] not in q["candidate_ids"]:
            continue
        if vectors is None:
            score = len(tokens(q["text"]) & tokens(c["text"]))
        else:
            if q["id"] not in vectors or c["id"] not in vectors:
                continue
            score = sum(a*b for a, b in zip(vectors[q["id"]], vectors[c["id"]], strict=True))
        scores.append((c["id"], score))
    return sorted(scores, key=lambda row: (-row[1], row[0]))


def calibrate(rows):
    # Predict answerable iff top score >= threshold; an absent response always abstains.
    scores = sorted({r["top_score"] for r in rows if r["top_score"] is not None})
    candidates = scores + ([math.nextafter(scores[-1], math.inf)] if scores else [1.0])
    def f1(t):
        tp = fp = fn = 0
        for r in rows:
            yes = r["top_score"] is not None and r["top_score"] >= t
            tp += yes and r["answerable"]
            fp += yes and not r["answerable"]
            fn += not yes and r["answerable"]
        return 2*tp/(2*tp+fp+fn) if 2*tp+fp+fn else 0
    return max(candidates, key=lambda t: (f1(t), t))


def evaluate(data, vectors=None):
    rows = []
    for q in data["queries"]:
        ranked = rank(q, data["chunks"], vectors)
        # A partial corpus cannot get credit by omitting competing candidates.
        if len(ranked) != len(q["candidate_ids"]):
            ranked = []
        top = [i for i, _ in ranked[:5]]
        relevant = q["relevant_ids"]
        hits = set(top) & set(relevant)
        rows.append({"id": q["id"], "direction": q["direction"], "split": q["split"],
                     "answerable": q["answerable"], "available": len(ranked) == len(q["candidate_ids"]),
                     "top_ids": top, "top_score": ranked[0][1] if ranked else None,
                     "hit5": int(bool(hits)), "recall5": len(hits)/len(relevant) if relevant else None,
                     "mrr5": next((1/(i+1) for i, c in enumerate(top) if c in relevant), 0)})
    threshold = calibrate([r for r in rows if r["split"] == "calibration"])
    groups = {}
    for split in ("calibration", "holdout"):
        for direction in ("all", "en-en", "id-id", "en-id", "id-en"):
            subset = [r for r in rows if r["split"] == split and r["answerable"] and (direction == "all" or r["direction"] == direction)]
            available = [r for r in subset if r["available"]]
            groups[f"{split}/{direction}"] = {"n": len(subset), "hit_count": sum(r["hit5"] for r in subset),
                "hit5": statistics.mean(r["hit5"] for r in subset),
                "recall5": statistics.mean(r["recall5"] for r in subset),
                "mrr5": statistics.mean(r["mrr5"] for r in subset),
                "available_n": len(available),
                "successful_request_hit5": statistics.mean(r["hit5"] for r in available) if available else None}
    negatives = [r for r in rows if r["split"] == "holdout" and not r["answerable"]]
    false_positive = sum(r["top_score"] is not None and r["top_score"] >= threshold for r in negatives)
    return {"rows": rows, "groups": groups, "calibration_threshold": threshold,
            "holdout_negative_n": len(negatives), "holdout_false_positive": false_positive,
            "negative_available_n": sum(r["available"] for r in negatives)}


def prepare():
    data, config = load()
    plan = schedule(data)
    cost = sum(len(rows)*2048*config["providers"][p]["usd_per_million_tokens"]/1e6 for p, _, rows in plan)
    retry_cost = config["max_retries_global"]*10*2048*max(p["usd_per_million_tokens"] for p in config["providers"].values())/1e6
    manifest = {"status": "PREPARED_OFFLINE_NOT_AUTHORIZED", "dataset_hash": digest(data), "config_hash": digest(config),
                "runner_hash": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                "planned_requests": len(plan), "max_attempts_with_retry": len(plan)+config["max_retries_global"],
                "max_requests": config["max_requests"], "cost_reservation_usd": cost+retry_cost,
                "cost_basis": "2048 tokens per item reservation, not measured token usage; synchronous standard pricing",
                "proposed_cap_usd": config["proposed_cap_usd"],
                "schedule": [{"provider": p, "stage": s, "ids": [r["id"] for r in rows]} for p, s, rows in plan]}
    assert manifest["max_attempts_with_retry"] <= config["max_requests"]
    assert manifest["cost_reservation_usd"] <= config["proposed_cap_usd"]
    save(OUT / "prepared_manifest.json", manifest)
    save(OUT / "lexical_baseline.json", evaluate(data))
    return manifest


def normalize(vector, dimensions):
    if not isinstance(vector, list) or len(vector) != dimensions:
        raise SafeFailure("invalid_vector_shape")
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in vector):
        raise SafeFailure("invalid_vector_value")
    norm = math.sqrt(sum(v*v for v in vector))
    if not math.isfinite(norm) or norm <= 0:
        raise SafeFailure("invalid_vector_norm")
    return [v/norm for v in vector]


def payload(provider, model, rows, document):
    if provider == "openai":
        return {"model": model, "input": [r["text"] for r in rows], "dimensions": 1536, "encoding_format": "float"}
    return {"requests": [{"model": f"models/{model}", "content": {"parts": [{"text": r["text"]}]},
                          "taskType": "RETRIEVAL_DOCUMENT" if document else "RETRIEVAL_QUERY",
                          "outputDimensionality": 1536} for r in rows]}


def parse_vectors(provider, response, n):
    try:
        if provider == "openai":
            rows = response["data"]
            if len(rows) != n or any(type(r["index"]) is not int for r in rows) or sorted(r["index"] for r in rows) != list(range(n)):
                raise SafeFailure("invalid_response_order")
            vectors = [r["embedding"] for r in sorted(rows, key=lambda r: r["index"])]
        else:
            vectors = [r["values"] for r in response["embeddings"]]
        if len(vectors) != n:
            raise SafeFailure("invalid_response_count")
        return [normalize(v, 1536) for v in vectors]
    except (KeyError, TypeError, ValueError, OverflowError, AttributeError):
        raise SafeFailure("invalid_response") from None


def live(manifest_hash, approved_cap):
    # Authorization must be explicit for this frozen manifest. Never called by prepare/tests.
    data, config = load()
    manifest = json.loads((OUT / "prepared_manifest.json").read_text(encoding="utf-8"))
    if digest(manifest) != manifest_hash or manifest["dataset_hash"] != digest(data) or manifest["config_hash"] != digest(config) or manifest["runner_hash"] != hashlib.sha256(Path(__file__).read_bytes()).hexdigest():
        raise SafeFailure("manifest_mismatch_prepare_again")
    if not math.isfinite(approved_cap) or not 0 < approved_cap <= config["proposed_cap_usd"]:
        raise SafeFailure("invalid_approved_cap")
    if approved_cap < manifest["cost_reservation_usd"]:
        raise SafeFailure("approved_cap_below_run_reservation")
    import httpx
    from dotenv import dotenv_values
    # Only two explicitly named credentials are retained; never print or serialize env values.
    dotenv = dotenv_values(ROOT / ".env", interpolate=False)
    keys = {p: os.environ.get(c["key_env"]) or dotenv.get(c["key_env"]) for p, c in config["providers"].items()}
    del dotenv
    if any(not isinstance(k, str) or not k.strip() for k in keys.values()):
        raise SafeFailure("missing_embedding_credentials")
    run = OUT / datetime.now(timezone.utc).strftime("run-%Y%m%dT%H%M%S-%fZ")
    ledger, vectors, blocked = [], {p: {} for p in keys}, set()
    reserved, retries, stop = 0.0, 0, False
    save(run / "manifest.json", manifest)
    with httpx.Client(timeout=30, follow_redirects=False, trust_env=False) as client:
        for p, stage, rows in schedule(data):
            if stop or p in blocked:
                continue
            model = config["providers"][p]["model"]
            url = "https://api.openai.com/v1/embeddings" if p == "openai" else f"https://generativelanguage.googleapis.com/v1beta/models/{model}:batchEmbedContents"
            headers = {"Authorization": f"Bearer {keys[p]}"} if p == "openai" else {"x-goog-api-key": keys[p]}
            for attempt in range(2):
                reserve = len(rows)*2048*config["providers"][p]["usd_per_million_tokens"]/1e6
                if len(ledger) >= config["max_requests"] or reserved+reserve > approved_cap:
                    stop = True
                    break
                if attempt:
                    if retries >= config["max_retries_global"]:
                        stop = True
                        break
                    retries += 1
                    time.sleep(2)
                reserved += reserve
                entry = {"provider": p, "stage": stage, "ids": [r["id"] for r in rows], "attempt": attempt+1,
                         "reserved_usd": reserve, "status": "reserved_unknown"}
                ledger.append(entry)
                save(run / "ledger.json", ledger)  # Persist reservation before network side effect.
                started, transient = time.perf_counter(), False
                try:
                    response = client.post(url, headers=headers, json=payload(p, model, rows, stage in {"corpus", "preflight-document"}))
                    entry["http_status"] = response.status_code
                    if response.status_code != 200:
                        transient = response.status_code in {408, 429, 500, 502, 503, 504}
                        raise SafeFailure("provider_http_error")
                    if len(response.content) > 2_000_000:
                        raise SafeFailure("oversized_response")
                    body = response.json()
                    parsed = parse_vectors(p, body, len(rows))
                    if p == "openai" and body.get("model") != model:
                        raise SafeFailure("unexpected_response_model")
                    metadata = body.get("usage" if p == "openai" else "usageMetadata")
                    if metadata is None:
                        metadata = {}
                    if not isinstance(metadata, dict):
                        raise SafeFailure("invalid_usage_metadata")
                    usage = metadata.get("prompt_tokens" if p == "openai" else "promptTokenCount")
                    if usage is not None and (type(usage) is not int or usage < 0):
                        raise SafeFailure("invalid_usage_metadata")
                    entry["reported_input_tokens"] = usage if type(usage) is int and usage >= 0 else None
                    if type(usage) is int and usage > len(rows)*2048:
                        raise SafeFailure("usage_exceeds_reservation_stop")
                    if stage in {"corpus", "query"}:
                        vectors[p].update({r["id"]: v for r, v in zip(rows, parsed, strict=True)})
                    entry["status"] = "ok"
                except httpx.TransportError:
                    transient, entry["status"] = True, "transport_error_unknown_charge"
                except SafeFailure as exc:
                    entry["status"] = str(exc)
                except (ValueError, KeyError, TypeError, AttributeError, OverflowError):
                    entry["status"] = "invalid_response"
                finally:
                    entry["elapsed_ms"] = round((time.perf_counter()-started)*1000, 3)
                    save(run / "ledger.json", ledger)
                if entry["status"] == "ok":
                    break
                if not transient or attempt == 1:
                    blocked.add(p)
                    if entry.get("http_status") == 429 or entry["status"] == "usage_exceeds_reservation_stop":
                        stop = True
                    break
    # No raw provider response or secret is retained. Vectors are synthetic evaluation data.
    for p in vectors:
        save(run / f"{p}_vectors.json", vectors[p])
        result = evaluate(data, vectors[p])
        complete = len(vectors[p]) == len(data["chunks"])+len(data["queries"])
        result.update({"status": "complete" if complete and p not in blocked and not stop else "incomplete",
                       "provider": p, "model": config["providers"][p]["model"], "dimensions": 1536})
        result["selection_gate_pass"] = (result["status"] == "complete"
            and result["groups"]["holdout/all"]["hit_count"] >= config["rules"]["holdout_hit_count_min"]
            and all(result["groups"][f"holdout/{d}"]["hit_count"] >= config["rules"]["per_direction_hit_count_min"]
                    for d in ("en-en", "id-id", "en-id", "id-en")))
        result["response_model_evidence"] = "response model verified" if p == "openai" else "requested model; response may omit model ID"
        result["holdout_answerable_intent_families"] = len({q["family"] for q in data["queries"] if q["split"] == "holdout" and q["answerable"]})
        result["sampling_note"] = "16 holdout rows from 4 intent families; directions/translations are dependent, no significance claim"
        result["latency"] = {}
        for stage in ("corpus", "query", "repeat"):
            times = sorted(e["elapsed_ms"] for e in ledger if e["provider"] == p and e["stage"] == stage and e["status"] == "ok")
            result["latency"][stage] = {"unit": "milliseconds per synchronous batch request", "n": len(times),
                "median": statistics.median(times) if times else None,
                "p95_nearest_rank": times[math.ceil(.95*len(times))-1] if times else None}
        save(run / f"{p}_retrieval.json", result)
    save(run / "summary.json", {"status": "incomplete" if stop or blocked else "complete_pending_review",
                                "attempts": len(ledger), "reserved_usd": reserved,
                                "actual_invoice_cost": "not_verified", "decision": "requires_review_no_automatic_winner"})
    print(f"Saved sanitized run evidence in configured output directory: {run.name}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["prepare", "live"], nargs="?", default="prepare")
    parser.add_argument("--approved-manifest")
    parser.add_argument("--approved-cap-usd", type=float)
    args = parser.parse_args()
    try:
        if args.mode == "prepare":
            manifest = prepare()
            print(json.dumps({"status": manifest["status"], "manifest_hash": digest(manifest),
                              "requests": manifest["planned_requests"], "max_attempts": manifest["max_attempts_with_retry"],
                              "reservation_usd": round(manifest["cost_reservation_usd"], 6)}))
        else:
            if not args.approved_manifest or args.approved_cap_usd is None:
                raise SafeFailure("live_requires_explicit_manifest_and_approved_cap")
            live(args.approved_manifest, args.approved_cap_usd)
    except (SafeFailure, AssertionError, OSError, ValueError, KeyError, TypeError) as exc:
        print(json.dumps({"status": "stopped", "reason": str(exc) if isinstance(exc, SafeFailure) else "invalid_local_inputs"}))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
