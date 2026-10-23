#!/usr/bin/env python3
"""Checks whether the ISSN mismatches of the identity audit (full_corpus_identity_mismatch_audit.py) are only print/online
variants of one journal: each distinct (citing ISSN, cache ISSN) pair is looked up in OpenAlex's sources API (field `issn_l`)
and classified as same ISSN-L family, distinct family, or not found. Reads EVAL_DB_PATH_BBL_TEX, EVAL_CACHE_DB_PATH, EVAL_RESULTS_ROOT
and the audit's keys file; writes issn_l_family_check.json (counts) to EVAL_OUTPUT_DIR/output_issn_l_family_check/. Only mismatches
where both ISSNs are present are checked; votes whose cache row has no ISSN are not ISSN-string mismatches and are left as they
are. Looked-up responses are cached in openalex_issnl_lookup_cache.json in the same directory; only an answer "not found"
(HTTP 404) is cached as not found, and the script stops if a lookup fails otherwise. A lookup that is not in the cache needs
EVAL_CONTACT_EMAIL (your own contact address, sent in the user agent to OpenAlex; at most four requests per second). The flag
--write-citation-keys adds the keys of the citations the check concerns (`restored_citation_keys`, `per_citation_detail`; read by
sibling_issnl_scopus_figures.py); the Makefile always passes it.
"""
from __future__ import annotations

import argparse
import json
import time
import urllib.error
import urllib.request
import sqlite3
from collections import Counter

from full_corpus_identity_mismatch_audit import get_conn
from identity_vote_rules import build_any_row_has_issn_index, classify_votes, format_issn, invalid_backends, normalize_issn
from leave_one_out_helpers import load_records
import stats_common
from stats_common import contact_user_agent, required_path_env, write_json

CACHE_DB_PATH = required_path_env("EVAL_CACHE_DB_PATH")
# the keys file written by full_corpus_identity_mismatch_audit.py
KEYS_PATH = required_path_env("EVAL_OUTPUT_DIR") / "output_identity_mismatch_audit" / "full_corpus_identity_mismatch_audit_keys.json"
OUTPUT_DIR = required_path_env("EVAL_OUTPUT_DIR") / "output_issn_l_family_check"
ISSNL_CACHE_PATH = OUTPUT_DIR / "openalex_issnl_lookup_cache.json"

stats_common.OUTPUT_DIR = OUTPUT_DIR

REQUEST_INTERVAL_S = 0.25  # at most 4 requests per second (OpenAlex allows 10)
RETRY_DELAY_S = 1.5


def load_issnl_cache() -> dict[str, dict | None]:
    if ISSNL_CACHE_PATH.exists():
        return json.loads(ISSNL_CACHE_PATH.read_text())
    return {}


def save_issnl_cache(cache: dict) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    ISSNL_CACHE_PATH.write_text(json.dumps(cache, indent=2), encoding="utf-8")


def openalex_lookup_issnl(issn_norm: str, cache: dict) -> dict | None:
    """Look up an ISSN's ISSN-L family via OpenAlex's sources-by-ISSN
    endpoint. issn_norm is the 8-char normalized form (no dash); OpenAlex
    wants the dashed form. Returns {"issn_l": ..., "display_name": ...} or
    None if not found (HTTP 404, cached) or if the lookup failed (not cached)."""
    if issn_norm in cache:
        return cache[issn_norm]

    dashed = format_issn(issn_norm)
    url = f"https://api.openalex.org/sources/issn:{dashed}"
    for attempt in range(3):
        time.sleep(REQUEST_INTERVAL_S)
        try:
            req = urllib.request.Request(url, headers={"User-Agent": contact_user_agent("issn_l_family_check.py")})
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            result = {
                "issn_l": data.get("issn_l"),
                "display_name": data.get("display_name"),
                "issn": data.get("issn"),
            }
            cache[issn_norm] = result
            return result
        except urllib.error.HTTPError as e:
            if e.code == 404:
                cache[issn_norm] = None
                return None
            time.sleep(RETRY_DELAY_S * (attempt + 1))
        except (urllib.error.URLError, TimeoutError, ConnectionError, json.JSONDecodeError):
            time.sleep(RETRY_DELAY_S * (attempt + 1))
    return None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write-citation-keys", action="store_true",
                        help="also write restored_citation_keys and per_citation_detail to the JSON file")
    args = parser.parse_args()
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    conn = get_conn()
    cache_conn = sqlite3.connect(str(CACHE_DB_PATH))

    audit_keys = json.loads(KEYS_PATH.read_text())
    audit_misattribution_keys = {tuple(k) for k in audit_keys["confirmed_misattribution_keys"]}
    print(f"loaded {len(audit_misattribution_keys)} confirmed_misattribution keys from the audit", flush=True)

    rows = conn.execute(
        "SELECT month, arxiv_id, suffix, entry_key, issn, predatory_list_hits "
        "FROM entries_confirmed WHERE final_assessment='predatory'"
    ).fetchall()
    targets = [(r["month"], r["arxiv_id"], r["suffix"], r["entry_key"]) for r in rows]
    records = load_records(targets)
    any_row_has_issn = build_any_row_has_issn_index(cache_conn)

    misattributed = []
    for r in rows:
        key = (r["month"], r["arxiv_id"], r["suffix"], r["entry_key"])
        citing_issn = normalize_issn(r["issn"])
        if citing_issn is None:
            continue
        winners = [w for w in (r["predatory_list_hits"] or "").split(",") if w]
        rec = records.get(key)
        if rec is None:
            continue

        per_backend, verdict = classify_votes(rec, winners, citing_issn, any_row_has_issn)
        if verdict != "confirmed_misattribution":
            continue
        invalid = invalid_backends(per_backend)
        misattributed.append({
            "key": key, "citing_issn": citing_issn,
            "per_backend": [pb for pb in per_backend if pb["backend"] in invalid],
        })

    print(f"re-derived confirmed_misattribution citations: {len(misattributed)}", flush=True)
    recomputed_keys = {m["key"] for m in misattributed}
    assert recomputed_keys == audit_misattribution_keys, (
        f"re-derivation mismatch vs the audit keys file: "
        f"{len(recomputed_keys - audit_misattribution_keys)} not in the audit, "
        f"{len(audit_misattribution_keys - recomputed_keys)} missing"
    )
    print("re-derivation matches the audit keys file exactly.", flush=True)

    # Collect every distinct ISSN needing ISSN-L resolution: both sides of
    # every issn_mismatch pair.
    mismatch_pairs = set()
    for m in misattributed:
        for pb in m["per_backend"]:
            if pb["type"] == "issn_mismatch":
                mismatch_pairs.add((m["citing_issn"], pb["cache_issn"]))
    print(f"distinct (citing_issn, cache_issn) issn_mismatch pairs to resolve: {len(mismatch_pairs)}", flush=True)

    distinct_issns = sorted({i for pair in mismatch_pairs for i in pair})
    print(f"distinct individual ISSNs to look up via OpenAlex: {len(distinct_issns)}", flush=True)

    issnl_cache = load_issnl_cache()
    n_fetched = 0
    for issn in distinct_issns:
        if issn not in issnl_cache:
            openalex_lookup_issnl(issn, issnl_cache)
            n_fetched += 1
            if n_fetched % 20 == 0:
                save_issnl_cache(issnl_cache)
                print(f"  looked up {n_fetched}/{len(distinct_issns)} new ISSNs...", flush=True)
    save_issnl_cache(issnl_cache)
    n_failed = sum(1 for issn in distinct_issns if issn not in issnl_cache)
    if n_failed:
        raise SystemExit(f"{n_failed} ISSN lookups failed (network or server error, not cached); run the script again to retry them")
    print(f"OpenAlex lookups complete ({n_fetched} newly fetched, {len(distinct_issns) - n_fetched} from cache).", flush=True)

    pair_resolution = {}
    for citing, cache_issn in mismatch_pairs:
        c_info = issnl_cache.get(citing)
        k_info = issnl_cache.get(cache_issn)
        c_l = c_info.get("issn_l") if c_info else None
        k_l = k_info.get("issn_l") if k_info else None
        if c_l and k_l:
            resolution = "same_issnl_family" if normalize_issn(c_l) == normalize_issn(k_l) else "confirmed_distinct_family"
        else:
            resolution = "unresolved_not_in_openalex"
        pair_resolution[(citing, cache_issn)] = {
            "citing_issn": format_issn(citing), "cache_issn": format_issn(cache_issn),
            "citing_issn_l": c_l, "cache_issn_l": k_l,
            "resolution": resolution,
        }

    resolution_dist = Counter(v["resolution"] for v in pair_resolution.values())
    print(f"pair resolution distribution: {dict(resolution_dist)}", flush=True)

    # A citation is included again when every invalid vote was a same-ISSN-L-family mismatch; votes whose cache row has
    # no ISSN stay invalid.
    included_again = []
    per_citation_detail = []
    for m in misattributed:
        still_invalid = []
        for pb in m["per_backend"]:
            if pb["type"] == "issn_mismatch":
                res = pair_resolution[(m["citing_issn"], pb["cache_issn"])]["resolution"]
                if res == "same_issnl_family":
                    continue  # this backend's vote is now treated as valid
                still_invalid.append({**pb, "issnl_resolution": res})
            else:
                still_invalid.append(pb)  # null_cache_issn (corroborated): unchanged, out of scope

        is_included_again = len(still_invalid) == 0
        if is_included_again:
            included_again.append(m["key"])
        per_citation_detail.append({
            "key": list(m["key"]), "citing_issn": format_issn(m["citing_issn"]),
            "invalid_votes": m["per_backend"],
            "invalid_votes_after_issn_l_check": still_invalid,
            "included_again_as_predatory": is_included_again,
        })

    print(f"citations included again as predatory after the ISSN-L check: {len(included_again)}", flush=True)

    # Submissions that lose their strict flag entirely through the audit and have it again through a citation that is included again.
    per_submission_strict = conn.execute(
        "SELECT month, arxiv_id, "
        "SUM(CASE WHEN final_assessment='predatory' THEN 1 ELSE 0 END) n_strict "
        "FROM entries_confirmed GROUP BY month, arxiv_id"
    ).fetchall()
    strict_drop_by_sub = Counter()
    for m in misattributed:
        strict_drop_by_sub[(m["key"][0], m["key"][1])] += 1
    included_again_by_sub = Counter()
    for key in included_again:
        included_again_by_sub[(key[0], key[1])] += 1

    n_subs_flag_included_again = 0
    for p in per_submission_strict:
        sub_key = (p["month"], p["arxiv_id"])
        n_dropped_by_audit = strict_drop_by_sub.get(sub_key, 0)
        if n_dropped_by_audit == 0:
            continue
        strict_without_inclusion = p["n_strict"] - n_dropped_by_audit
        strict_with_inclusion = strict_without_inclusion + included_again_by_sub.get(sub_key, 0)
        if strict_without_inclusion == 0 and strict_with_inclusion > 0:
            n_subs_flag_included_again += 1

    out = {
        "n_confirmed_misattribution_citations": len(misattributed),
        "n_distinct_issn_mismatch_pairs": len(mismatch_pairs),
        "n_distinct_issns_looked_up": len(distinct_issns),
        "pair_resolution_distribution": dict(resolution_dist),
        "n_citations_included_again_as_predatory": len(included_again),
        "n_submissions_with_strict_flag_included_again": n_subs_flag_included_again,
    }
    if args.write_citation_keys:
        out["restored_citation_keys"] = sorted(list(k) for k in included_again)
        out["per_citation_detail"] = per_citation_detail
    write_json("issn_l_family_check", out)
    print(json.dumps({k: v for k, v in out.items() if k not in ("per_citation_detail", "restored_citation_keys")}, indent=2, default=str))


if __name__ == "__main__":
    main()
