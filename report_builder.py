"""
Deterministic (no-AI) report builder.

Parses the data map text (the same "CODE = description (1=Label, 2=Label)"
format produced by datamap_parser.py, or a simpler manually-pasted
"CODE = description" list) into a code -> plain-English label lookup, then
walks every one of the 12 QC check results plus the topline data and
produces a full plain-English report - "Awareness - Jeep: 81%", "8% of
respondents are speeding", etc. - instead of raw column codes.

This entirely replaces the AI-generated summary/email for now. No network
calls, nothing that can fail for lack of an API key.
"""
import re


# ---------------------------------------------------------------------------
# Data map lookup
# ---------------------------------------------------------------------------
def parse_data_map_lookup(data_map_text: str) -> dict:
    """
    Returns {code: {"label": str, "value_labels": {"1": "Female", ...}}}
    """
    lookup = {}
    if not data_map_text:
        return lookup
    for line in data_map_text.splitlines():
        line = line.strip()
        if not line or "=" not in line:
            continue
        code, rest = line.split("=", 1)
        code = code.strip()
        rest = rest.strip()
        if not code:
            continue

        value_labels = {}
        m = re.search(r'\(([^()]*)\)\s*$', rest)
        if m and "=" in m.group(1):
            vl_block = m.group(1)
            rest = rest[:m.start()].strip()
            for pair in vl_block.split(","):
                pair = pair.strip()
                if "=" in pair:
                    k, v = pair.split("=", 1)
                    value_labels[k.strip()] = v.strip()

        lookup[code] = {"label": rest, "value_labels": value_labels}
    return lookup


def label_for(lookup: dict, code: str) -> str:
    """Plain-English label for a column code, or a clearly-marked fallback
    if it isn't in the data map (never silently shows a bare cryptic code)."""
    entry = lookup.get(code)
    if entry and entry.get("label"):
        return entry["label"]
    return f"{code} (not in your data map)"


def value_label_for(lookup: dict, code: str, value) -> str:
    """Translates a single response code (e.g. '1') to its label ('Female')
    for a given column, if the data map has that mapping."""
    entry = lookup.get(code)
    if entry:
        vl = entry.get("value_labels", {})
        sval = str(value)
        if sval in vl:
            return vl[sval]
    return str(value)


# ---------------------------------------------------------------------------
# Report sections
# ---------------------------------------------------------------------------
def _fmt_pct(x) -> str:
    try:
        return f"{float(x):g}%"
    except (TypeError, ValueError):
        return str(x)


def _section_completion(lookup, completion: dict) -> list[str]:
    details = completion.get("details", [])
    if not details:
        return []
    flagged = [d for d in details if d.get("flag")]
    lines = [f"**Completion:** {len(flagged)} of {len(details)} question(s) checked have a high blank rate (>5%)."]
    for d in flagged:
        label = label_for(lookup, d["question"])
        lines.append(f"- {label}: {_fmt_pct(d['blank_pct'])} blank ({d['answered']}/{d['total']} answered)")
    if not flagged and details:
        lines.append("- No completion issues found. ✅")
    return lines


def _section_straight_lining(lookup, sl: dict) -> list[str]:
    details = sl.get("details", [])
    if not details:
        return []
    flagged = [d for d in details if d.get("flag")]
    lines = [f"**Straight-Lining:** {len(flagged)} of {len(details)} grid(s) checked show high straight-lining (>10%)."]
    for d in details:
        status = "⚠️" if d.get("flag") else "✅"
        lines.append(f"- {d['grid']}: {_fmt_pct(d['pct'])} straight-lined "
                      f"({d['straight_liners']}/{d['total_respondents']}) {status}")
    return lines


def _section_speeding(speeding: dict) -> list[str]:
    if "speeders_pct" not in speeding:
        return []
    status = "⚠️ Flagged" if speeding.get("flag") else "✅ OK"
    return [f"**Speeding:** {_fmt_pct(speeding['speeders_pct'])} of respondents "
            f"({speeding['speeders_count']}) completed in under 30% of the median time "
            f"({speeding['median_duration_seconds']:.0f}s median). {status}"]


def _section_quota(quota: dict) -> list[str]:
    details = quota.get("details", [])
    if not details:
        return []
    status_icon = {"FULL": "🔵", "LAGGING": "🟡", "LAGGING_BADLY": "🔴", "on_track": "✅"}
    lines = ["**Quota Status:**"]
    for d in details:
        if d.get("status") == "MISSING_COLUMN":
            lines.append(f"- {d.get('quota_group')} = {d.get('quota_label')}: column not found in data")
            continue
        icon = status_icon.get(d["status"], "")
        lines.append(f"- {icon} {d['quota_group']} = {d['quota_label']}: {d['filled']}/{d['target_n']} "
                      f"({_fmt_pct(d['fill_pct'])}) — {d['status'].replace('_', ' ').title()}")
    return lines


def _section_duplicates(dup: dict) -> list[str]:
    if "details" not in dup and "full_row_duplicates" not in dup:
        return []
    lines = ["**Duplicate / Fraud Check:**"]
    for d in dup.get("details", []):
        if d.get("status") == "MISSING_COLUMN":
            continue
        status = "⚠️" if d.get("flag") else "✅"
        lines.append(f"- {d['field']}: {d['affected_respondents']} respondent(s) share a value "
                      f"({_fmt_pct(d['pct'])}) {status}")
    if dup.get("full_row_duplicates", 0) > 0:
        lines.append(f"- ⚠️ {dup['full_row_duplicates']} fully duplicate response rows "
                      f"({_fmt_pct(dup['full_row_duplicate_pct'])})")
    elif "full_row_duplicates" in dup:
        lines.append("- No fully duplicate rows found. ✅")
    return lines if len(lines) > 1 else []


def _section_open_end(lookup, oe: dict) -> list[str]:
    details = oe.get("details", [])
    if not details:
        return []
    lines = ["**Open-End Quality:**"]
    for d in details:
        if d.get("status") in ("MISSING_COLUMN", "NO_RESPONSES"):
            continue
        label = label_for(lookup, d["question"])
        status = "⚠️" if d.get("flag") else "✅"
        lines.append(f"- {label}: {_fmt_pct(d['too_short_pct'])} too short, "
                      f"{_fmt_pct(d['copy_pasted_pct'])} copy-pasted {status}")
    return lines if len(lines) > 1 else []


def _section_consistency(lookup, lc: dict) -> list[str]:
    details = lc.get("details", [])
    flagged = [d for d in details if d.get("flag")]
    if not details:
        return []
    lines = [f"**Logical Consistency:** {len(flagged)} of {len(details)} rule(s) found contradictions."]
    for d in flagged:
        lines.append(f"- {d['label']}: {d['contradictions']}/{d['base']} respondents ({_fmt_pct(d['pct'])})")
    if not flagged:
        lines.append("- No logical contradictions found. ✅")
    return lines


def _section_outliers(lookup, out: dict) -> list[str]:
    details = out.get("details", [])
    flagged = [d for d in details if d.get("flag")]
    if not details:
        return []
    lines = [f"**Outliers:** {len(flagged)} of {len(details)} column(s) have out-of-range values."]
    for d in flagged:
        label = label_for(lookup, d["column"])
        lines.append(f"- {label}: {d['out_of_range_count']} respondent(s) outside "
                      f"[{d['valid_range'][0]:g}, {d['valid_range'][1]:g}] ({_fmt_pct(d['out_of_range_pct'])})")
    return lines


def _section_skip_logic(sk: dict) -> list[str]:
    details = sk.get("details", [])
    flagged = [d for d in details if d.get("flag")]
    if not details:
        return []
    lines = [f"**Skip Logic:** {len(flagged)} of {len(details)} rule(s) violated."]
    for d in flagged:
        lines.append(f"- {d['label']}: {d['violations']}/{d['base']} respondents answered "
                      f"a question they should have skipped ({_fmt_pct(d['pct'])})")
    if not flagged:
        lines.append("- No skip-logic violations found. ✅")
    return lines


def _section_response_distribution(lookup, rd: dict) -> list[str]:
    details = rd.get("details", [])
    flagged = [d for d in details if d.get("flag")]
    if not details:
        return []
    lines = [f"**Response Distribution:** {len(flagged)} of {len(details)} question(s) "
             f"have one answer dominating ({rd.get('threshold_pct', 80)}%+)."]
    for d in flagged:
        label = label_for(lookup, d["question"])
        lines.append(f"- {label}: '{d['top_value']}' chosen by {_fmt_pct(d['top_value_pct'])} of respondents")
    return lines


def _section_attention(at: dict) -> list[str]:
    details = at.get("details", [])
    flagged = [d for d in details if d.get("flag")]
    if not details:
        return []
    lines = [f"**Attention Checks:** {len(flagged)} of {len(details)} check(s) had failures."]
    for d in flagged:
        lines.append(f"- {d['label']}: {d['failed']}/{d['base']} respondents failed ({_fmt_pct(d['pct'])})")
    if not flagged:
        lines.append("- All attention checks passed. ✅")
    return lines


def _section_wave_spike(ws: dict) -> list[str]:
    details = ws.get("details", []) if ws else []
    if not details:
        return []
    flagged = [d for d in details if d.get("flag")]
    lines = [f"**Wave-over-Wave:** {len(flagged)} of {len(details)} metric(s) shifted "
             f"{ws.get('threshold_pts', 10)}+ points vs. the previous wave."]
    for d in details:
        status = "⚠️" if d.get("flag") else "✅"
        lines.append(f"- {d['question']} ({d['metric']}): {d['previous_wave']:g} → {d['current_wave']:g} "
                      f"({d['difference']:+g}) {status}")
    return lines


def _section_custom_qc(cqc: dict) -> list[str]:
    if not cqc or cqc.get("rules_evaluated", 0) == 0:
        return []
    lines = ["**Custom QC Rules:**"]
    for r in cqc.get("rule_results", []):
        if r["type"] == "plain_english":
            note = " (plain-English rules need AI, which is currently disabled - re-enable AI to evaluate this one)"
            lines.append(f"- {r['label']}: not evaluated{note}")
            continue
        status = "⚠️" if r["flagged_count"] > 0 else "✅"
        rows = f" — rows: {', '.join(str(x) for x in r['flagged_rows'])}" if r["flagged_count"] else ""
        lines.append(f"- {r['label']}: {r['flagged_count']} flagged{rows} {status}")
    return lines


def _wave_delta(current_entry: dict, previous_entry: dict):
    """
    Compares one topline metric between two waves. Returns
    (current_display_value, delta_points, delta_str) or None if not comparable.
    For a % distribution, compares the current wave's leading category against
    that SAME category code in the previous wave (so it's always comparing
    like-for-like, e.g. "Top-2-Box=1" vs "Top-2-Box=1", not just whichever
    happened to be on top each time).
    """
    cur_dist = current_entry.get("distribution")
    prev_dist = previous_entry.get("distribution") if previous_entry else None
    if cur_dist:
        top_code = max(cur_dist, key=cur_dist.get)
        cur_val = cur_dist[top_code]
        if prev_dist and top_code in prev_dist:
            prev_val = prev_dist[top_code]
            delta = round(cur_val - prev_val, 1)
            sign = "+" if delta >= 0 else ""
            return top_code, cur_val, f"{sign}{delta} pts"
        return top_code, cur_val, None
    if "mean" in current_entry:
        cur_val = current_entry["mean"]
        if previous_entry and "mean" in previous_entry:
            delta = round(cur_val - previous_entry["mean"], 2)
            sign = "+" if delta >= 0 else ""
            return None, cur_val, f"{sign}{delta}"
        return None, cur_val, None
    return None, None, None


def _section_topline(lookup, topline: dict, max_items: int = 60,
                      previous_topline: dict | None = None,
                      current_wave_label: str = "", previous_wave_label: str = "") -> list[str]:
    if not topline:
        return []
    lines = []
    count = 0
    for code, data in topline.items():
        if count >= max_items:
            lines.append(f"... and {len(topline) - max_items} more (see raw results below).")
            break
        entry = lookup.get(code)
        if not entry or not entry.get("label"):
            continue  # skip unmapped columns in the topline section - too noisy otherwise
        label = entry["label"]
        n = data.get("n", 0)
        prev_data = (previous_topline or {}).get(code)

        if "distribution" in data:
            vc = data["distribution"]
            top_sorted = sorted(vc.items(), key=lambda kv: -kv[1])[:5]
            parts = []
            for code_val, pct in top_sorted:
                val_label = value_label_for(lookup, code, code_val)
                parts.append(f"{val_label} {_fmt_pct(pct)}")
            line = f"- **{label}**: " + ", ".join(parts) + f" (n={n})"
            if "mean" in data:
                line += f" — mean {data['mean']:g}"
            top_code, cur_val, delta_str = _wave_delta(data, prev_data)
            if delta_str and top_code:
                top_label = value_label_for(lookup, code, top_code)
                line += (f" — {delta_str} vs {previous_wave_label} for \"{top_label}\" "
                         f"({_fmt_pct(prev_data['distribution'][top_code])})")
            lines.append(line)
        elif "mean" in data:
            line = (f"- **{label}**: mean {data['mean']:g}, median {data['median']:g}, "
                    f"range {data['min']:g}–{data['max']:g} (n={n})")
            _, _, delta_str = _wave_delta(data, prev_data)
            if delta_str:
                line += f" — {delta_str} vs {previous_wave_label} mean ({prev_data['mean']:g})"
            lines.append(line)
        count += 1

    if not lines:
        return []
    header = "**Topline Findings** (only columns present in your data map are shown here)"
    if previous_topline:
        header += f", compared against **{previous_wave_label}**"
    return [header + ":"] + lines


def _wave_comparison_narrative(lookup, topline: dict, previous_topline: dict,
                                current_wave_label: str, previous_wave_label: str,
                                max_items: int = 8) -> list[str]:
    """
    The plain-English paragraph version: "Wave 2 data indicates that the
    sponsorship awareness for Brand XYZ is 64%, +8 pts from Wave 1 (56%)."
    One sentence per mapped metric that exists in both waves, most-changed first.
    """
    if not topline or not previous_topline:
        return []

    rows = []
    for code, data in topline.items():
        entry = lookup.get(code)
        if not entry or not entry.get("label"):
            continue
        prev_data = previous_topline.get(code)
        if not prev_data:
            continue
        top_code, cur_val, delta_str = _wave_delta(data, prev_data)
        if cur_val is None or delta_str is None:
            continue
        try:
            delta_num = float(delta_str.replace(" pts", ""))
        except ValueError:
            delta_num = 0.0
        is_pct = top_code is not None
        rows.append((abs(delta_num), entry["label"], cur_val, delta_str, is_pct))

    if not rows:
        return []

    rows.sort(key=lambda r: -r[0])
    rows = rows[:max_items]

    sentences = []
    for _, label, cur_val, delta_str, is_pct in rows:
        cur_str = _fmt_pct(cur_val) if is_pct else f"{cur_val:g}"
        sentences.append(f"**{label}** is **{cur_str}** ({delta_str} vs {previous_wave_label})")

    intro = f"**{current_wave_label} data indicates that** " + "; ".join(sentences) + "."
    return [intro]


# ---------------------------------------------------------------------------
# Full report
# ---------------------------------------------------------------------------
def build_summary_report(results: dict, data_map_text: str, study_name: str, checkpoint_pct: int,
                          previous_topline: dict | None = None,
                          current_wave_label: str = "This wave",
                          previous_wave_label: str = "the previous wave") -> str:
    lookup = parse_data_map_lookup(data_map_text)

    header = [
        f"# {study_name} — QC & Topline Report",
        f"**Wave:** {current_wave_label}  ",
        f"**Checkpoint:** {'Soft Launch' if checkpoint_pct == 0 else f'{checkpoint_pct}% Full Launch'}  ",
        f"**Total respondents:** {results.get('total_respondents', '—')}",
        "",
    ]
    if not lookup:
        header.append("_No data map was provided for this project, so column codes are shown as-is "
                       "below - add a data map in Step 2 to see plain-English labels instead._")
        header.append("")

    sections = []
    sections += _section_completion(lookup, results.get("completion", {}))
    sections += _section_straight_lining(lookup, results.get("straight_lining", {}))
    sections += _section_speeding(results.get("speeding", {}))
    sections += _section_quota(results.get("quota", {}))
    sections += _section_duplicates(results.get("duplicates", {}))
    sections += _section_open_end(lookup, results.get("open_end_quality", {}))
    sections += _section_consistency(lookup, results.get("logical_consistency", {}))
    sections += _section_outliers(lookup, results.get("outliers", {}))
    sections += _section_skip_logic(results.get("skip_logic", {}))
    sections += _section_response_distribution(lookup, results.get("response_distribution", {}))
    sections += _section_attention(results.get("attention_traps", {}))
    if "wave_spike" in results:
        sections += _section_wave_spike(results.get("wave_spike", {}))
    sections += _section_custom_qc(results.get("custom_qc", {}))

    body = "\n".join(sections) if sections else "_No checks were configured for this run._"

    topline = results.get("topline", {})

    narrative_lines = []
    if previous_topline:
        narrative_lines = _wave_comparison_narrative(lookup, topline, previous_topline,
                                                       current_wave_label, previous_wave_label)

    topline_lines = _section_topline(lookup, topline, previous_topline=previous_topline,
                                      current_wave_label=current_wave_label,
                                      previous_wave_label=previous_wave_label)
    topline_block = "\n".join(topline_lines) if topline_lines else ""

    parts = header + ["## Data Quality Checks", body]
    if narrative_lines:
        parts += ["", "## Wave-over-Wave Summary", "\n\n".join(narrative_lines)]
    if topline_block:
        parts += ["", topline_block]
    return "\n".join(parts)


def build_email_draft(study_name: str, checkpoint_pct: int, issues: list[str]) -> str:
    """Simple deterministic vendor/field-team email - no AI needed."""
    if not issues:
        return "No issues detected at this checkpoint - no email needed."
    stage = "Soft Launch" if checkpoint_pct == 0 else f"{checkpoint_pct}% Full Launch"
    lines = [
        f"Subject: {study_name} — Data Quality Issues at {stage}",
        "",
        "Hi team,",
        "",
        f"During QC review of {study_name} at the {stage} checkpoint, the following "
        f"data quality issue(s) were flagged and need attention:",
        "",
    ]
    lines += [f"- {issue}" for issue in issues]
    lines += [
        "",
        "Please review and let us know how you'd like to proceed.",
        "",
        "Thanks,",
        "QC Team",
    ]
    return "\n".join(lines)
