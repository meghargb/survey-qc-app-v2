"""
Core data-quality-check logic.

These functions expect a pandas DataFrame loaded from the uploaded
CSV/Excel file. Column names are configurable so you can match them
to however your survey platform (Qualtrics, Decipher, Forsta, etc.)
names its columns.
"""
import pandas as pd
import numpy as np


# ---------------------------------------------------------------------------
# 1. COMPLETION CHECK  (soft launch: "is every question answered?")
# ---------------------------------------------------------------------------
def completion_check(df: pd.DataFrame, question_columns: list[str]) -> dict:
    """
    Checks, per question, what % of respondents left it blank.
    Flags any question with unusually high blank/skip rate.
    """
    results = []
    for col in question_columns:
        if col not in df.columns:
            results.append({"question": col, "status": "MISSING_COLUMN"})
            continue
        total = len(df)
        answered = int(df[col].notna().sum())
        blank_pct = float(round(100 * (total - answered) / total, 1)) if total else 0.0
        flag = bool(blank_pct > 5)  # more than 5% blank is worth a look
        results.append({
            "question": col,
            "answered": int(answered),
            "total": int(total),
            "blank_pct": blank_pct,
            "flag": flag,
        })
    return {"check": "completion", "details": results}


# ---------------------------------------------------------------------------
# 2. STRAIGHT-LINING CHECK  (grid/matrix questions where respondent picks
#    the same answer for every row - a sign of low-quality/fraudulent data)
# ---------------------------------------------------------------------------
def straight_lining_check(df: pd.DataFrame, grid_column_groups: dict[str, list[str]]) -> dict:
    """
    grid_column_groups: e.g. {"Q5_grid": ["Q5_1", "Q5_2", "Q5_3", "Q5_4"]}
    Flags respondents who gave the identical answer across all items in a grid.
    """
    results = []
    for grid_name, cols in grid_column_groups.items():
        cols_present = [c for c in cols if c in df.columns]
        if len(cols_present) < 2:
            continue
        sub = df[cols_present]
        straight = int((sub.nunique(axis=1) == 1).sum())
        pct = float(round(100 * straight / len(df), 1)) if len(df) else 0.0
        results.append({
            "grid": grid_name,
            "straight_liners": straight,
            "total_respondents": len(df),
            "pct": pct,
            "flag": bool(pct > 10),  # more than 10% straight-lining is a red flag
        })
    return {"check": "straight_lining", "details": results}


# ---------------------------------------------------------------------------
# 3. SPEEDING CHECK  (respondents finishing far faster than typical)
# ---------------------------------------------------------------------------
def speeding_check(df: pd.DataFrame, duration_col: str = "duration_seconds",
                    speed_threshold_ratio: float = 0.3) -> dict:
    """
    Flags anyone whose completion time is less than `speed_threshold_ratio`
    of the median completion time (default: under 30% of median = speeder).
    """
    if duration_col not in df.columns:
        return {"check": "speeding", "details": [], "note": f"Column '{duration_col}' not found"}
    median_time = df[duration_col].median()
    threshold = median_time * speed_threshold_ratio
    speeders = df[df[duration_col] < threshold]
    pct = float(round(100 * len(speeders) / len(df), 1)) if len(df) else 0.0
    return {
        "check": "speeding",
        "median_duration_seconds": float(median_time),
        "threshold_seconds": float(threshold),
        "speeders_count": int(len(speeders)),
        "speeders_pct": pct,
        "flag": bool(pct > 5),
    }


# ---------------------------------------------------------------------------
# 4. QUOTA CHECK  (sub, hard, demographic quotas)
# ---------------------------------------------------------------------------
def quota_check(df: pd.DataFrame, quota_rules: list[dict], current_pct_complete: int) -> dict:
    """
    quota_rules: list of dicts like:
      {"quota_group": "Gender", "quota_label": "Male", "column_name": "gender",
       "match_value": "Male", "target_n": 200, "quota_type": "hard"}

    Returns fill status per quota, plus warnings for quotas that are lagging
    given how far along fieldwork is (current_pct_complete).
    """
    results = []
    warnings = []
    for rule in quota_rules:
        col = rule["column_name"]
        if col not in df.columns:
            results.append({**rule, "status": "MISSING_COLUMN"})
            continue
        filled = int((df[col].astype(str) == str(rule["match_value"])).sum())
        target = int(rule["target_n"])
        remaining = max(target - filled, 0)
        fill_pct = float(round(100 * filled / target, 1)) if target else 0.0

        status = "on_track"
        if fill_pct >= 100:
            status = "FULL"
        elif current_pct_complete >= 75 and fill_pct < 75:
            status = "LAGGING_BADLY"
        elif current_pct_complete >= 50 and fill_pct < 40:
            status = "LAGGING"

        entry = {
            "quota_group": rule["quota_group"],
            "quota_label": rule["quota_label"],
            "quota_type": rule.get("quota_type", "soft"),
            "target_n": target,
            "filled": filled,
            "remaining": remaining,
            "fill_pct": fill_pct,
            "status": status,
        }
        results.append(entry)

        if status == "FULL":
            warnings.append(f"{rule['quota_group']} = {rule['quota_label']} is FULL "
                             f"({filled}/{target}). Close this quota to avoid overfill.")
        elif status in ("LAGGING", "LAGGING_BADLY"):
            warnings.append(f"{rule['quota_group']} = {rule['quota_label']} is lagging "
                             f"({filled}/{target}, {fill_pct}%) at {current_pct_complete}% "
                             f"fieldwork complete. Needs attention.")

    return {"check": "quota", "checkpoint_pct": current_pct_complete,
             "details": results, "warnings": warnings}


# ---------------------------------------------------------------------------
# 5. WAVE-OVER-WAVE SPIKE CHECK  (compares a metric between wave 1 and wave 2+)
# ---------------------------------------------------------------------------
def wave_spike_check(current_df: pd.DataFrame, previous_df: pd.DataFrame,
                      compare_columns: list[str], spike_threshold_pts: float = 10.0) -> dict:
    """
    For each column (typically a brand-awareness / top-2-box style question
    with values like Yes/No or 1-5), compares the % positive response
    between previous wave and current wave. Flags swings bigger than
    `spike_threshold_pts` percentage points.

    This is a simple % comparison for categorical/binary questions.
    For numeric scale questions, it compares the mean instead.
    """
    results = []
    for col in compare_columns:
        if col not in current_df.columns or col not in previous_df.columns:
            results.append({"question": col, "status": "MISSING_COLUMN"})
            continue

        is_numeric = pd.api.types.is_numeric_dtype(current_df[col])

        if is_numeric:
            prev_val = float(previous_df[col].mean())
            curr_val = float(current_df[col].mean())
            metric = "mean"
        else:
            # % of respondents giving the most common (or "positive") response
            top_value = previous_df[col].mode().iloc[0] if not previous_df[col].mode().empty else None
            prev_val = float(round(100 * (previous_df[col] == top_value).mean(), 1))
            curr_val = float(round(100 * (current_df[col] == top_value).mean(), 1))
            metric = f"% = '{top_value}'"

        diff = float(round(curr_val - prev_val, 1))
        flag = bool(abs(diff) >= spike_threshold_pts)

        results.append({
            "question": col,
            "metric": metric,
            "previous_wave": prev_val,
            "current_wave": curr_val,
            "difference": diff,
            "flag": flag,
        })
    return {"check": "wave_spike", "threshold_pts": spike_threshold_pts, "details": results}


# ---------------------------------------------------------------------------
# 6. DUPLICATE / FRAUD CHECK  (same IP, email, or entire response duplicated)
# ---------------------------------------------------------------------------
def duplicate_check(df: pd.DataFrame, id_columns: list[str] | None = None) -> dict:
    """
    id_columns: e.g. ["ip_address", "email"]. Flags respondents who share a
    value in any of these columns with another respondent (likely the same
    person taking the survey more than once).
    Also flags fully-duplicate rows (identical answers across every column),
    which is a strong fraud signal even without an IP/email column.
    """
    results = []
    for col in (id_columns or []):
        if col not in df.columns:
            results.append({"field": col, "status": "MISSING_COLUMN"})
            continue
        vc = df[df[col].notna() & (df[col].astype(str).str.strip() != "")][col].value_counts()
        dupes = vc[vc > 1]
        dup_respondents = int(dupes.sum())
        pct = float(round(100 * dup_respondents / len(df), 1)) if len(df) else 0.0
        results.append({
            "field": col,
            "duplicate_values": int(len(dupes)),
            "affected_respondents": dup_respondents,
            "pct": pct,
            "flag": bool(dup_respondents > 0),
        })

    full_dupes = int(df.duplicated(keep=False).sum())
    full_dupe_pct = float(round(100 * full_dupes / len(df), 1)) if len(df) else 0.0

    return {
        "check": "duplicates",
        "details": results,
        "full_row_duplicates": full_dupes,
        "full_row_duplicate_pct": full_dupe_pct,
        "flag": bool(full_dupes > 0),
    }


# ---------------------------------------------------------------------------
# 7. OPEN-END QUALITY CHECK  (too-short or copy-pasted verbatim responses)
# ---------------------------------------------------------------------------
def open_end_quality_check(df: pd.DataFrame, open_end_columns: list[str] | None = None,
                            min_length: int = 10) -> dict:
    """
    Flags open-ended text responses that are suspiciously short (gibberish
    like "n/a", "asdf", "good") or identical across multiple respondents
    (copy-pasted / bot-generated text).
    """
    results = []
    for col in (open_end_columns or []):
        if col not in df.columns:
            results.append({"question": col, "status": "MISSING_COLUMN"})
            continue

        text = df[col].dropna().astype(str).str.strip()
        text = text[text != ""]
        total = len(text)
        if total == 0:
            results.append({"question": col, "status": "NO_RESPONSES"})
            continue

        too_short = int((text.str.len() < min_length).sum())
        short_pct = float(round(100 * too_short / total, 1))

        # copy-pasted: same exact text (case-insensitive) used by 2+ respondents
        vc = text.str.lower().value_counts()
        copy_pasted_groups = vc[vc > 1]
        copy_pasted_respondents = int(copy_pasted_groups.sum())
        copy_pct = float(round(100 * copy_pasted_respondents / total, 1))

        results.append({
            "question": col,
            "total_responses": total,
            "too_short_count": too_short,
            "too_short_pct": short_pct,
            "copy_pasted_count": copy_pasted_respondents,
            "copy_pasted_pct": copy_pct,
            "flag": bool(short_pct > 20 or copy_pct > 5),
        })
    return {"check": "open_end_quality", "min_length": min_length, "details": results}


# ---------------------------------------------------------------------------
# 8. LOGICAL CONSISTENCY CHECK  (contradictory answers)
# ---------------------------------------------------------------------------
def logical_consistency_check(df: pd.DataFrame, consistency_rules: list[dict] | None = None) -> dict:
    """
    consistency_rules: list of dicts like:
      {"label": "Never used brand but rates it highly",
       "if_col": "brand_usage", "if_value": "Never used",
       "then_col": "brand_satisfaction", "then_invalid_values": ["9", "10"]}

    Flags respondents whose answer to `then_col` is in `then_invalid_values`
    given that `if_col` == `if_value` (a logical contradiction).
    """
    results = []
    for rule in (consistency_rules or []):
        if_col, then_col = rule.get("if_col"), rule.get("then_col")
        if if_col not in df.columns or then_col not in df.columns:
            results.append({**rule, "status": "MISSING_COLUMN"})
            continue

        mask_if = df[if_col].astype(str) == str(rule.get("if_value"))
        invalid_values = [str(v) for v in rule.get("then_invalid_values", [])]
        mask_then = df[then_col].astype(str).isin(invalid_values)
        contradictions = int((mask_if & mask_then).sum())
        base = int(mask_if.sum())
        pct = float(round(100 * contradictions / base, 1)) if base else 0.0

        results.append({
            "label": rule.get("label", f"{if_col} vs {then_col}"),
            "if_col": if_col, "if_value": rule.get("if_value"),
            "then_col": then_col, "then_invalid_values": rule.get("then_invalid_values", []),
            "contradictions": contradictions,
            "base": base,
            "pct": pct,
            "flag": bool(contradictions > 0),
        })
    return {"check": "logical_consistency", "details": results}


# ---------------------------------------------------------------------------
# 9. OUTLIER CHECK  (numeric answers outside a plausible range)
# ---------------------------------------------------------------------------
def outlier_check(df: pd.DataFrame, numeric_ranges: dict[str, list[float]] | None = None) -> dict:
    """
    numeric_ranges: e.g. {"age": [18, 99], "household_income": [0, 1000000]}
    Flags respondents whose value falls outside [min, max] for that column.
    """
    results = []
    for col, bounds in (numeric_ranges or {}).items():
        if col not in df.columns:
            results.append({"column": col, "status": "MISSING_COLUMN"})
            continue
        lo, hi = float(bounds[0]), float(bounds[1])
        numeric_col = pd.to_numeric(df[col], errors="coerce")
        out_of_range = numeric_col[(numeric_col.notna()) & ((numeric_col < lo) | (numeric_col > hi))]
        count = int(len(out_of_range))
        pct = float(round(100 * count / len(df), 1)) if len(df) else 0.0
        results.append({
            "column": col,
            "valid_range": [lo, hi],
            "out_of_range_count": count,
            "out_of_range_pct": pct,
            "example_values": [float(v) for v in out_of_range.head(5).tolist()],
            "flag": bool(count > 0),
        })
    return {"check": "outliers", "details": results}


# ---------------------------------------------------------------------------
# 10. SKIP LOGIC CHECK  (answered a question that should've been skipped)
# ---------------------------------------------------------------------------
def skip_logic_check(df: pd.DataFrame, skip_rules: list[dict] | None = None) -> dict:
    """
    skip_rules: list of dicts like:
      {"label": "Non-buyers shouldn't see purchase-experience question",
       "skip_if_col": "purchased", "skip_if_value": "No",
       "should_be_blank_col": "purchase_experience_rating"}

    Flags respondents who met the skip condition but still have a non-blank
    answer in the column they should have skipped.
    """
    results = []
    for rule in (skip_rules or []):
        skip_col, target_col = rule.get("skip_if_col"), rule.get("should_be_blank_col")
        if skip_col not in df.columns or target_col not in df.columns:
            results.append({**rule, "status": "MISSING_COLUMN"})
            continue

        mask_should_skip = df[skip_col].astype(str) == str(rule.get("skip_if_value"))
        mask_answered_anyway = df[target_col].notna() & (df[target_col].astype(str).str.strip() != "")
        violations = int((mask_should_skip & mask_answered_anyway).sum())
        base = int(mask_should_skip.sum())
        pct = float(round(100 * violations / base, 1)) if base else 0.0

        results.append({
            "label": rule.get("label", f"{skip_col} -> {target_col}"),
            "skip_if_col": skip_col, "skip_if_value": rule.get("skip_if_value"),
            "should_be_blank_col": target_col,
            "violations": violations,
            "base": base,
            "pct": pct,
            "flag": bool(violations > 0),
        })
    return {"check": "skip_logic", "details": results}


# ---------------------------------------------------------------------------
# 11. RESPONSE DISTRIBUTION CHECK  (one answer option dominating unnaturally)
# ---------------------------------------------------------------------------
def response_distribution_check(df: pd.DataFrame, question_columns: list[str] | None = None,
                                 dominant_threshold: float = 80.0) -> dict:
    """
    Flags questions where a single answer option is chosen by an unnaturally
    high % of respondents - often a sign of confusing wording, a scripting
    bug defaulting everyone to one answer, or straight-lining across the
    whole sample rather than just within a grid.
    """
    results = []
    for col in (question_columns or []):
        if col not in df.columns:
            results.append({"question": col, "status": "MISSING_COLUMN"})
            continue
        answered = df[col].dropna()
        answered = answered[answered.astype(str).str.strip() != ""]
        if len(answered) == 0:
            results.append({"question": col, "status": "NO_RESPONSES"})
            continue
        vc = answered.value_counts(normalize=True) * 100
        top_value = vc.index[0]
        top_pct = float(round(vc.iloc[0], 1))
        results.append({
            "question": col,
            "top_value": str(top_value),
            "top_value_pct": top_pct,
            "flag": bool(top_pct >= dominant_threshold),
        })
    return {"check": "response_distribution", "threshold_pct": dominant_threshold, "details": results}


# ---------------------------------------------------------------------------
# 12. ATTENTION TRAP CHECK  (respondents who fail a planted attention-check
#     question, e.g. "Please select 'Strongly Agree' for this item")
# ---------------------------------------------------------------------------
def attention_trap_check(df: pd.DataFrame, attention_rules: list[dict] | None = None) -> dict:
    """
    attention_rules: list of dicts like:
      {"label": "Attention check Q7", "column": "Q7_attention", "expected_value": "Strongly Agree"}

    Flags respondents who answered anything other than the expected value
    (blank answers are excluded - that's caught by the completion check).
    """
    results = []
    for rule in (attention_rules or []):
        col = rule.get("column")
        if col not in df.columns:
            results.append({**rule, "status": "MISSING_COLUMN"})
            continue
        answered = df[col].notna() & (df[col].astype(str).str.strip() != "")
        failed = answered & (df[col].astype(str) != str(rule.get("expected_value")))
        failed_count = int(failed.sum())
        base = int(answered.sum())
        pct = float(round(100 * failed_count / base, 1)) if base else 0.0
        results.append({
            "label": rule.get("label", f"Attention check: {col}"),
            "column": col,
            "expected_value": rule.get("expected_value"),
            "failed": failed_count,
            "base": base,
            "pct": pct,
            "flag": bool(failed_count > 0),
        })
    return {"check": "attention_traps", "details": results}


# ---------------------------------------------------------------------------
# 13. TOPLINE SUMMARY  (actual response content, not QC flags - this is what
#     lets the AI describe what the data SAYS, e.g. "brand sponsorship is
#     performing well", not just what's wrong with it)
# ---------------------------------------------------------------------------
def topline_summary(df: pd.DataFrame, columns: list[str] | None = None,
                     max_columns: int = 80, max_options_per_question: int = 20,
                     categorical_cardinality_threshold: int = 20) -> dict:
    """
    For each question column, returns the response distribution (top answer
    options and their %). Numeric columns also get mean/median. This is the
    actual substance of the data - separate from QC flags - so the AI can
    describe what respondents said, not just what's wrong with the data.

    Survey data is full of numerically-CODED categorical variables (Gender
    coded 1-4, Region coded 1-4, a 1-5 rating scale, a 0/1 Top-2-Box flag,
    etc). Those are just as "numeric" as age or income, but reporting only
    their mean is close to useless - you need the % breakdown per code too.
    So: any column with a small number of distinct values (<= threshold)
    gets a real category % distribution computed, on top of mean/median if
    it's also numeric. Only genuinely continuous fields (age, income,
    duration - many distinct values) fall back to mean/median only.
    """
    cols = columns if columns else [c for c in df.columns]
    cols = cols[:max_columns]  # keep prompt size sane on wide datasets

    summary = {}
    for col in cols:
        if col not in df.columns:
            continue
        series = df[col].dropna()
        series = series[series.astype(str).str.strip() != ""]
        if len(series) == 0:
            continue

        numeric = pd.to_numeric(series, errors="coerce")
        is_numeric = numeric.notna().mean() > 0.9
        n_unique = series.nunique()
        is_low_cardinality = n_unique <= categorical_cardinality_threshold

        entry = {"n": int(len(series))}

        if is_numeric:
            entry["mean"] = float(round(numeric.mean(), 2))
            entry["median"] = float(round(numeric.median(), 2))
            entry["min"] = float(numeric.min())
            entry["max"] = float(numeric.max())

        if (not is_numeric) or is_low_cardinality:
            vc = (series.value_counts(normalize=True) * 100).round(1)
            top = vc.head(max_options_per_question)
            entry["distribution"] = {str(k): float(v) for k, v in top.items()}

        if is_numeric and "distribution" in entry:
            entry["type"] = "coded_categorical"  # numeric-coded but few distinct values - report both
        elif is_numeric:
            entry["type"] = "numeric"
        else:
            entry["type"] = "categorical"

        summary[col] = entry
    return summary


# ---------------------------------------------------------------------------
# Run everything together
# ---------------------------------------------------------------------------


def custom_qc_check(df: pd.DataFrame, custom_rules: list) -> dict:
    """
    Evaluates custom QC rules against each respondent.
    Each rule has:
      - type: "conditional" or "plain_english"
      - label: user-friendly name for the rule
      - For conditional rules:
          conditions: list of {column, operator, value} joined by AND/OR
          join: "AND" | "OR"
      - For plain_english rules:
          description: the plain English rule text
          ai_result: pre-evaluated list of flagged row indices (filled by main.py before calling this)
    Returns flagged respondent row numbers and a summary per rule.
    """
    if not custom_rules:
        return {"rules_evaluated": 0, "flagged_rows": [], "rule_results": [], "issues": []}

    all_flagged = set()
    rule_results = []
    issues = []

    for rule in custom_rules:
        label = rule.get("label", "Custom Rule")
        rule_type = rule.get("type", "conditional")
        flagged = []

        if rule_type == "conditional":
            conditions = rule.get("conditions", [])
            join = rule.get("join", "AND").upper()
            masks = []
            for cond in conditions:
                col = cond.get("column", "")
                op = cond.get("operator", "equals")
                val = cond.get("value", "")
                if col not in df.columns:
                    continue
                try:
                    if op == "equals":
                        mask = df[col].astype(str).str.strip() == str(val).strip()
                    elif op == "not_equals":
                        mask = df[col].astype(str).str.strip() != str(val).strip()
                    elif op == "contains":
                        mask = df[col].astype(str).str.contains(str(val), case=False, na=False)
                    elif op == "not_contains":
                        mask = ~df[col].astype(str).str.contains(str(val), case=False, na=False)
                    elif op == "greater_than":
                        mask = pd.to_numeric(df[col], errors="coerce") > float(val)
                    elif op == "less_than":
                        mask = pd.to_numeric(df[col], errors="coerce") < float(val)
                    elif op == "greater_than_or_equal":
                        mask = pd.to_numeric(df[col], errors="coerce") >= float(val)
                    elif op == "less_than_or_equal":
                        mask = pd.to_numeric(df[col], errors="coerce") <= float(val)
                    elif op == "is_empty":
                        mask = df[col].isna() | (df[col].astype(str).str.strip() == "")
                    elif op == "is_not_empty":
                        mask = ~(df[col].isna() | (df[col].astype(str).str.strip() == ""))
                    else:
                        continue
                    masks.append(mask)
                except Exception:
                    continue

            if masks:
                if join == "OR":
                    combined = masks[0]
                    for m in masks[1:]:
                        combined = combined | m
                else:  # AND
                    combined = masks[0]
                    for m in masks[1:]:
                        combined = combined & m
                flagged = [int(i) + 1 for i in df[combined].index.tolist()]

        elif rule_type == "plain_english":
            # ai_result is pre-filled by main.py with flagged row numbers
            flagged = rule.get("ai_result", [])

        count = len(flagged)
        all_flagged.update(flagged)
        rule_results.append({
            "label": label,
            "type": rule_type,
            "flagged_count": int(count),
            "flagged_rows": flagged[:50],
        })
        if count > 0:
            issues.append(f"Custom rule '{label}': {count} respondent(s) flagged.")

    return {
        "rules_evaluated": int(len(custom_rules)),
        "flagged_rows": sorted(all_flagged)[:100],
        "rule_results": rule_results,
        "issues": issues,
    }

def run_all_checks(df: pd.DataFrame, config: dict, checkpoint_pct: int,
                    previous_wave_df: pd.DataFrame | None = None) -> dict:
    """
    config example:
    {
      "question_columns": [...],
      "grid_column_groups": {...},
      "duration_col": "duration_seconds",
      "quota_rules": [...],
      "wave_compare_columns": [...],
      "id_columns": ["ip_address", "email"],
      "open_end_columns": [...],
      "consistency_rules": [...],
      "numeric_ranges": {...},
      "skip_rules": [...],
      "distribution_columns": [...]
    }
    """
    out = {"checkpoint_pct": checkpoint_pct}

    out["completion"] = completion_check(df, config.get("question_columns", []))
    out["straight_lining"] = straight_lining_check(df, config.get("grid_column_groups", {}))
    out["speeding"] = speeding_check(df, config.get("duration_col", "duration_seconds"))
    out["quota"] = quota_check(df, config.get("quota_rules", []), checkpoint_pct)
    out["duplicates"] = duplicate_check(df, config.get("id_columns", []))
    out["open_end_quality"] = open_end_quality_check(df, config.get("open_end_columns", []))
    out["logical_consistency"] = logical_consistency_check(df, config.get("consistency_rules", []))
    out["outliers"] = outlier_check(df, config.get("numeric_ranges", {}))
    out["skip_logic"] = skip_logic_check(df, config.get("skip_rules", []))
    out["response_distribution"] = response_distribution_check(
        df, config.get("distribution_columns", []))
    out["attention_traps"] = attention_trap_check(df, config.get("attention_rules", []))
    out["topline"] = topline_summary(df, config.get("question_columns") or None)
    out["total_respondents"] = int(len(df))

    if previous_wave_df is not None and config.get("wave_compare_columns"):
        out["wave_spike"] = wave_spike_check(
            df, previous_wave_df, config["wave_compare_columns"]
        )

    # Roll up a simple list of all flags/warnings for the AI email + summary step
    issues = []
    for c in out["completion"]["details"]:
        if c.get("flag"):
            issues.append(f"Question '{c['question']}' has high blank rate: {c['blank_pct']}%")
    for s in out["straight_lining"]["details"]:
        if s.get("flag"):
            issues.append(f"Grid '{s['grid']}' has {s['pct']}% straight-lining respondents")
    if out["speeding"].get("flag"):
        issues.append(f"{out['speeding']['speeders_pct']}% of respondents are speeding "
                       f"through the survey")
    issues.extend(out["quota"]["warnings"])
    if "wave_spike" in out:
        for w in out["wave_spike"]["details"]:
            if w.get("flag"):
                issues.append(f"Spike detected on '{w['question']}': "
                               f"{w['previous_wave']} -> {w['current_wave']} "
                               f"({w['difference']:+} pts) vs previous wave")
    for d in out["duplicates"]["details"]:
        if d.get("flag"):
            issues.append(f"{d['affected_respondents']} respondents share a duplicate "
                           f"'{d['field']}' ({d['pct']}%)")
    if out["duplicates"].get("flag"):
        issues.append(f"{out['duplicates']['full_row_duplicates']} fully duplicate "
                       f"response rows found ({out['duplicates']['full_row_duplicate_pct']}%)")
    for o in out["open_end_quality"]["details"]:
        if o.get("flag"):
            issues.append(f"Open-end '{o['question']}': {o['too_short_pct']}% too short, "
                           f"{o['copy_pasted_pct']}% copy-pasted")
    for lc in out["logical_consistency"]["details"]:
        if lc.get("flag"):
            issues.append(f"Logical contradiction: {lc['label']} "
                           f"({lc['contradictions']}/{lc['base']} respondents, {lc['pct']}%)")
    for ol in out["outliers"]["details"]:
        if ol.get("flag"):
            issues.append(f"'{ol['column']}' has {ol['out_of_range_count']} out-of-range "
                           f"values ({ol['out_of_range_pct']}%), expected {ol['valid_range']}")
    for sl in out["skip_logic"]["details"]:
        if sl.get("flag"):
            issues.append(f"Skip logic violation: {sl['label']} "
                           f"({sl['violations']}/{sl['base']} respondents, {sl['pct']}%)")
    for rd in out["response_distribution"]["details"]:
        if rd.get("flag"):
            issues.append(f"'{rd['question']}' is dominated by answer '{rd['top_value']}' "
                           f"at {rd['top_value_pct']}% of responses")
    for at in out["attention_traps"]["details"]:
        if at.get("flag"):
            issues.append(f"Attention trap failed: {at['label']} "
                           f"({at['failed']}/{at['base']} respondents, {at['pct']}%)")

    out["issues"] = issues
    return out
