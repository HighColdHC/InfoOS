"""Screen only the XHS evidence coverage confirmed by the read-only probe."""
from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit, urlunsplit

from backend.domain.sources.monetization_prefilter import has_strong_hit, xhs_text_hits


def screen_record(record: dict[str, Any], rules: dict[str, Any]) -> dict[str, Any]:
    hits = [hit for field in rules["coverage"]
            if isinstance(record.get(field), str)
            for hit in xhs_text_hits(record[field], field, rules)]
    # Successful note field/value output contains content even when empty;
    # tags are optional. A title-only feed row cannot prove a miss.
    detail = record.get("_opencli_detail_record")
    detail_ok = (isinstance(detail, list)
                 and any(isinstance(row, dict) and row.get("field") == "content"
                         and isinstance(row.get("value"), str) for row in detail)
                 and isinstance(record.get("content"), str)
                 and not record.get("_opencli_detail_error"))
    evidence = {
        "rules_version": rules["rules_version"],
        "status": "matched" if has_strong_hit(hits) else "unknown",
        "hits": hits, "coverage": rules["coverage"], "detail_ok": detail_ok,
        "profile_checked": False, "profile_ok": None, "pinned_ok": None, "comments_checked": False, "comments_ok": None,
        "demand_comment_count": 0, "mentions": [], "signals": {},
        "unavailable_signals": ["author_profile", "groups", "shop", "pinned", "mentions", "comments"],
    }
    if not has_strong_hit(hits):
        comments = record.get("first_screen_comments")
        comments_ok = (record.get("comments_ok") is True and isinstance(comments, list)
                       and all(isinstance(c, dict) and isinstance(c.get("text"), str)
                               and type(c.get("is_author")) is bool for c in comments))
        evidence.update(comments_checked=True, comments_ok=comments_ok,
                        coverage=[*rules["coverage"], "comments"])
        if comments_ok:
            evidence["unavailable_signals"].remove("comments")
            demand_hits = [xhs_text_hits(c["text"], "comment_demand", rules, comments=True)
                           for c in comments if not c["is_author"]]
            evidence["demand_comment_count"] = sum(has_strong_hit(h) for h in demand_hits)
            evidence["hits"] += [dict(pair) for pair in dict.fromkeys(
                tuple(hit.items()) for hits in demand_hits for hit in hits)]
            if evidence["demand_comment_count"] >= rules["demand_min_comments"]:
                evidence["status"] = "matched"
    return {**record, "prefilter": evidence}


def apply_profile(record: dict[str, Any], profile: dict[str, Any] | None,
                  rules: dict[str, Any]) -> dict[str, Any]:
    evidence = {**record["prefilter"]}
    if evidence["status"] == "matched":
        return record  # Note text or first-screen comments already matched.
    profile = profile or {}
    profile_ok = profile.get("profile_ok") is True and isinstance(profile.get("description"), str)
    pinned = profile.get("pinned_titles")
    pinned_ok = (profile.get("pinned_ok") is True and isinstance(pinned, list)
                 and all(isinstance(title, str) and title for title in pinned))
    evidence.update(profile_checked=profile.get("profile_checked", False), profile_ok=profile_ok,
                    pinned_ok=pinned_ok, profile_user_id=record.get("author_user_id", ""),
                    profile_error=profile.get("error"), coverage=[*evidence["coverage"], *rules["profile_coverage"]],
                    unavailable_signals=["mentions"],
                    signals={"shop_entry": profile.get("shop_entry"), "group_entry": profile.get("group_entry")})
    for key in ("profile_error_detail", "profile_batch_index", "profile_batch_error"):
        if key in profile:
            evidence[key] = profile[key]
    signature = profile["description"] if profile_ok else ""
    titles = pinned if pinned_ok else []
    hits = xhs_text_hits(signature, "author_signature", rules)
    hits += [hit for title in titles for hit in xhs_text_hits(title, "author_pinned", rules)]
    if profile.get("group_entry") is True:
        hits.append({"field": "author_group_entry", "token": "visible", "strength": "strong"})
    evidence["hits"] = [dict(pair) for pair in dict.fromkeys(tuple(hit.items()) for hit in [*evidence["hits"], *hits])]
    evidence["status"] = ("matched" if has_strong_hit(hits) else "missed"
                          if evidence["detail_ok"] and profile_ok and pinned_ok
                          and evidence["comments_ok"] is True else "unknown")
    if not profile_ok:
        evidence["unavailable_signals"].append("author_profile")
    if not pinned_ok:
        evidence["unavailable_signals"].append("pinned")
    if evidence["comments_ok"] is not True:
        evidence["unavailable_signals"].append("comments")
    return {**record, "prefilter": evidence, "author_signature": signature, "author_pinned": titles}


def run_evidence(records: list[dict[str, Any]], rules: dict[str, Any]) -> dict[str, Any]:
    summary = {"rules_version": rules["rules_version"], "checked": len(records),
               "matched": 0, "missed": 0, "unknown": 0, "skipped": 0, "token_hits": {},
               "profiles_checked": 0, "profiles_ok": 0, "profiles_attempted_unknown": 0, "profile_hit_items": 0,
               "profile_batches": 0, "profile_batches_failed": 0, "profile_batch_errors": {},
               "comments_checked_items": 0, "comments_ok_items": 0, "comment_hit_items": 0,
               "mention_targets": 0, "mention_profiles_ok": 0, "mention_hit_items": 0,
               "mention_pinned_checked": 0, "mention_pinned_ok": 0, "mention_pinned_hit_items": 0}
    misses = []
    checked_profiles, ok_profiles, unknown_attempts = set(), set(), set()
    batches = {}
    for row in records:
        result = row["prefilter"]
        if "profile_batch_index" in result:
            batches[result["profile_batch_index"]] = result.get("profile_batch_error")
        summary[result["status"]] += 1
        user = result.get("profile_user_id")
        if user and result.get("profile_checked") is True:
            checked_profiles.add(user)
        if user and result.get("profile_checked") is None:
            unknown_attempts.add(user)
        if user and result.get("profile_ok") is True:
            ok_profiles.add(user)
        summary["profile_hit_items"] += any(h["field"] in rules["profile_coverage"] for h in result["hits"])
        summary["comments_checked_items"] += result["comments_checked"] is True
        summary["comments_ok_items"] += result["comments_ok"] is True
        summary["comment_hit_items"] += any(h["field"] == "comment_demand" for h in result["hits"])
        for token in {hit["token"] for hit in result["hits"]}:
            summary["token_hits"][token] = summary["token_hits"].get(token, 0) + 1
        if result["status"] == "missed":
            url = urlsplit(str(row.get("url") or ""))
            misses.append({"note_id": row.get("id", ""), "url": urlunsplit(url._replace(query="", fragment="")),
                           "title": str(row.get("title") or "")[:300], "author": row.get("author", ""),
                           **{key: row.get(key, "") for key in ("likes", "collects", "comments")},
                           "hits": result["hits"], "rules_version": rules["rules_version"], "coverage": result["coverage"]})
    summary.update(profiles_checked=len(checked_profiles), profiles_ok=len(ok_profiles),
                   profiles_attempted_unknown=len(unknown_attempts))
    summary["profile_batches"] = len(batches)
    for error in batches.values():
        if error:
            summary["profile_batches_failed"] += 1
            summary["profile_batch_errors"][error] = summary["profile_batch_errors"].get(error, 0) + 1
    return {"prefilter_misses": misses, "prefilter_summary": summary}
