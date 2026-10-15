"""
Setup wizard: Step 1 (Project) -> Step 2 (Questionnaire & Data Map +
Advanced check config + data file) -> Step 3 (Quota Sheet) -> Finish &
Run Check, which performs the same logic the old /waves/{id}/upload_and_check
FastAPI route did, then hands off to the dashboard.
"""
import os
import json
import shutil
from datetime import datetime

import pandas as pd
import streamlit as st
from openpyxl import Workbook
import io

import models
import checks as qc
import datamap_parser
import report_builder
import db_helpers as dbh

UPLOAD_DIR = "uploaded_files"


def _df_records(df: pd.DataFrame) -> list[dict]:
    """Drop fully-blank rows from a data_editor result and return clean records."""
    if df is None or df.empty:
        return []
    df = df.dropna(how="all")
    return df.to_dict(orient="records")


def _editable_table(key: str, columns: dict, help_text: str = "") -> pd.DataFrame:
    """columns: {col_name: default_value} defines the empty starting shape."""
    if key not in st.session_state:
        st.session_state[key] = pd.DataFrame(columns=list(columns.keys()))
    if help_text:
        st.caption(help_text)
    edited = st.data_editor(
        st.session_state[key], num_rows="dynamic", key=f"{key}_editor",
        width="stretch", hide_index=True,
    )
    st.session_state[key] = edited
    return edited


def _lines_to_list(text: str) -> list[str]:
    return [l.strip() for l in (text or "").splitlines() if l.strip()]


def render(db):
    st.title("📊 Survey Data QC Tool")
    step = st.session_state.wizard_step
    st.caption(f"Step {step} of 3")

    if step == 1:
        _step1_project(db)
    elif step == 2:
        _step2_questionnaire(db)
    elif step == 3:
        _step3_quota(db)


# ===========================================================================
# STEP 1 - PROJECT
# ===========================================================================
def _step1_project(db):
    st.header("1. Project")

    mode = st.radio("Project", ["New Project", "Existing Project"], horizontal=True,
                     index=0 if st.session_state.project_mode == "new" else 1)
    st.session_state.project_mode = "new" if mode == "New Project" else "existing"

    if st.session_state.project_mode == "new":
        name = st.text_input("Project name", placeholder="e.g. Tennis Brand Tracker Q3",
                              key="new_study_name")
        wave_type = st.radio("Wave type", ["Single Wave", "Multiple Wave"], horizontal=True,
                              key="new_wave_type_radio")
        wave_label = st.text_input(
            "Wave name (optional)", placeholder="e.g. Pre-Wave, Wave 1, Baseline",
            key="new_wave_label",
            help="Name this wave however you track it - \"Wave 1\", \"Pre-Wave\", \"Baseline\", etc. "
                 "Leave blank to just call it \"Wave 1\".")
        sample_size = st.number_input("Target sample size", min_value=0, step=1,
                                       key="new_sample_size")

        if st.button("Continue →", type="primary", disabled=not name.strip()):
            study = dbh.create_study(db, name.strip())
            wave = dbh.create_wave(
                db, study.id, wave_number=1,
                wave_type="single" if wave_type == "Single Wave" else "multiple",
                sample_size=int(sample_size) if sample_size else None,
                wave_label=wave_label.strip() if wave_label else None,
            )
            st.session_state.current_study_id = study.id
            st.session_state.current_wave_id = wave.id
            st.session_state.wizard_step = 2
            st.rerun()

    else:
        studies = dbh.list_studies(db)
        if not studies:
            st.info("No existing projects yet - create a New Project instead.")
            return

        study_options = {f"{s.name} (#{s.id})": s.id for s in studies}
        chosen = st.selectbox("Select project", list(study_options.keys()))
        study_id = study_options[chosen]

        wave_mode = st.radio("Add data to this project as", ["New Wave", "Existing Wave (new checkpoint)"],
                              horizontal=True)
        st.session_state.wave_mode = "new" if wave_mode == "New Wave" else "fetch"

        if st.session_state.wave_mode == "new":
            existing_waves = dbh.list_waves(db, study_id)
            next_num = max([w.wave_number for w in existing_waves], default=0) + 1
            wave_number = st.number_input("New wave number", min_value=1, value=next_num, step=1)
            wave_label = st.text_input(
                "Wave name (optional)", placeholder="e.g. Post-Wave, Wave 2, Follow-up",
                key="existing_wave_label",
                help="Leave blank to just call it \"Wave {}\".".format(int(wave_number)))
            wave_type = st.radio("Wave type", ["Single Wave", "Multiple Wave"], horizontal=True,
                                  key="existing_wave_type_radio")
            sample_size = st.number_input("Target sample size", min_value=0, step=1,
                                           key="existing_sample_size")

            if st.button("Continue →", type="primary"):
                wave = dbh.create_wave(
                    db, study_id, wave_number=int(wave_number),
                    wave_type="single" if wave_type == "Single Wave" else "multiple",
                    sample_size=int(sample_size) if sample_size else None,
                    wave_label=wave_label.strip() if wave_label else None,
                )
                st.session_state.current_study_id = study_id
                st.session_state.current_wave_id = wave.id
                st.session_state.wizard_step = 2
                st.rerun()
        else:
            waves = dbh.list_waves(db, study_id)
            if not waves:
                st.info("This project has no waves yet - use 'New Wave' instead.")
                return
            wave_options = {w.display_name: w.id for w in waves}
            chosen_wave = st.selectbox("Select wave to add a new checkpoint to", list(wave_options.keys()))

            if st.button("Continue →", type="primary"):
                st.session_state.current_study_id = study_id
                st.session_state.current_wave_id = wave_options[chosen_wave]
                st.session_state.wizard_step = 2
                st.rerun()


# ===========================================================================
# STEP 2 - QUESTIONNAIRE & DATA MAP + ADVANCED CHECK CONFIG + DATA FILE
# ===========================================================================
def _step2_questionnaire(db):
    st.header("2. Questionnaire & Data")

    study = dbh.get_study(db, st.session_state.current_study_id)

    with st.expander("🗺️ Data Map (optional, but recommended)", expanded=True):
        st.caption(
            "Every survey names its variables differently - one might call awareness "
            "\"R1\", another calls it \"Aval\". Tell the system what each variable means "
            "once, here, and every AI feature (Ask AI, Custom QC Rules, summaries) will "
            "understand your plain-English questions without you needing to know the "
            "exact column names."
        )
        existing_map = study.data_map or ""
        data_map_text = st.text_area(
            "Paste your data map (one per line, e.g. R1 = Awareness for Brand X)",
            value=st.session_state.get("data_map_text_input", existing_map),
            height=140, key="data_map_text_input",
        )
        data_map_file = st.file_uploader(
            "...or upload a data map file (CSV/Excel, e.g. a Decipher-style Datamap export)",
            type=["csv", "xlsx", "xls"], key="data_map_file_uploader",
        )

    decipher_link = st.text_input("Decipher link (optional)", value=study.decipher_link or "",
                                   key="decipher_link_input")
    questionnaire_file = st.file_uploader("Questionnaire file (optional)", key="questionnaire_file_uploader")

    st.divider()
    st.subheader("Data file — current checkpoint")
    st.caption("If you set a target sample size in Step 1, the actual % complete is "
               "calculated automatically from your data - this dropdown just marks "
               "Soft Launch vs. Full Launch.")
    checkpoint_choice = st.selectbox(
        "Checkpoint", ["Soft Launch", "Full Launch — 25%", "Full Launch — 50%",
                       "Full Launch — 75%", "Full Launch — 100%"],
        key="checkpoint_choice",
    )
    checkpoint_pct = {"Soft Launch": 0, "Full Launch — 25%": 25, "Full Launch — 50%": 50,
                       "Full Launch — 75%": 75, "Full Launch — 100%": 100}[checkpoint_choice]
    data_file = st.file_uploader("Data file (CSV or Excel)", type=["csv", "xlsx", "xls"],
                                  key="data_file_uploader")

    with st.expander("⚙️ Advanced — configure QC checks (all optional)"):
        st.markdown("**Completion / Response Distribution — question columns to check**")
        question_columns_text = st.text_area(
            "One column name per line", key="cfg_question_columns_text",
            help="Used by the Completion check and Response Distribution check.")

        st.markdown("**Straight-Lining — grid column groups**")
        grid_df = _editable_table("cfg_grid_groups", {"grid_name": "", "columns_comma_separated": ""},
                                   "One row per grid, columns comma-separated, e.g. Q5_1,Q5_2,Q5_3")

        st.markdown("**Speeding — duration column**")
        duration_col = st.text_input("Column name", value="duration_seconds", key="cfg_duration_col")

        st.markdown("**Wave-over-Wave — columns to compare against the previous wave**")
        wave_compare_text = st.text_area("One column per line", key="cfg_wave_compare_text")

        st.markdown("**Duplicate / Fraud — ID columns**")
        id_columns_text = st.text_area("One column per line (e.g. ip_address, email)",
                                        key="cfg_id_columns_text")

        st.markdown("**Open-End Quality — open-text columns**")
        open_end_text = st.text_area("One column per line", key="cfg_open_end_text")

        st.markdown("**Logical Consistency rules**")
        consistency_df = _editable_table(
            "cfg_consistency_rules",
            {"label": "", "if_col": "", "if_value": "", "then_col": "", "then_invalid_values_comma_sep": ""},
            "Flags respondents where if_col == if_value AND then_col is one of then_invalid_values.")

        st.markdown("**Outlier ranges**")
        outlier_df = _editable_table("cfg_numeric_ranges", {"column": "", "min": None, "max": None},
                                      "One row per numeric column with a valid min/max.")

        st.markdown("**Skip Logic rules**")
        skip_df = _editable_table(
            "cfg_skip_rules",
            {"label": "", "skip_if_col": "", "skip_if_value": "", "should_be_blank_col": ""},
            "Flags respondents who met the skip condition but still answered anyway.")

        st.markdown("**Response Distribution — extra columns to check**")
        distribution_text = st.text_area(
            "One column per line (in addition to the completion columns above)",
            key="cfg_distribution_text")

        st.markdown("**Attention Trap questions**")
        attention_df = _editable_table(
            "cfg_attention_rules", {"label": "", "column": "", "expected_value": ""},
            "Flags respondents who didn't select the expected value.")

        st.markdown("**🔍 Custom QC Rules**")
        st.caption("Condition Builder rules: rows sharing the same Rule label are grouped "
                   "together (joined by the Join column - AND/OR).")
        custom_cond_df = _editable_table(
            "cfg_custom_condition_rules",
            {"rule_label": "", "join": "AND", "column": "", "operator": "equals", "value": ""},
            "operator: equals, not_equals, contains, not_contains, greater_than, less_than, "
            "greater_than_or_equal, less_than_or_equal, is_empty, is_not_empty")
        custom_plain_df = _editable_table(
            "cfg_custom_plain_rules", {"label": "", "description": ""},
            "Plain-English rules - the AI reads your description and the data to flag rows.")

    col_a, col_b = st.columns(2)
    with col_a:
        if st.button("← Back"):
            st.session_state.wizard_step = 1
            st.rerun()
    with col_b:
        if st.button("Continue →", type="primary"):
            _save_questionnaire_and_datamap(db, study, decipher_link, data_map_text,
                                             data_map_file, questionnaire_file)
            st.session_state.checkpoint_pct_pending = checkpoint_pct
            if data_file is not None:
                st.session_state.pending_data_file_bytes = data_file.getvalue()
                st.session_state.pending_data_file_name = data_file.name
            st.session_state.wizard_step = 3
            st.rerun()


def _save_questionnaire_and_datamap(db, study, decipher_link, data_map_text, data_map_file, questionnaire_file):
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    if decipher_link:
        study.decipher_link = decipher_link

    if questionnaire_file is not None:
        path = os.path.join(UPLOAD_DIR, f"study{study.id}_questionnaire_{questionnaire_file.name}")
        with open(path, "wb") as f:
            f.write(questionnaire_file.getvalue())
        study.questionnaire_file_path = path

    combined_map_parts = []
    if data_map_text and data_map_text.strip():
        combined_map_parts.append(data_map_text.strip())

    if data_map_file is not None:
        map_path = os.path.join(UPLOAD_DIR, f"study{study.id}_datamap_{data_map_file.name}")
        with open(map_path, "wb") as f:
            f.write(data_map_file.getvalue())
        try:
            parsed = datamap_parser.parse_decipher_datamap(map_path)
            if parsed:
                combined_map_parts.append(parsed)
            else:
                map_df = pd.read_csv(map_path) if map_path.lower().endswith(".csv") else pd.read_excel(map_path)
                cols = list(map_df.columns)[:2]
                if len(cols) >= 2:
                    lines = [f"{row[cols[0]]} = {row[cols[1]]}" for _, row in map_df.iterrows()
                             if pd.notna(row[cols[0]]) and pd.notna(row[cols[1]])]
                    combined_map_parts.append("\n".join(lines))
        except Exception as e:
            combined_map_parts.append(f"(Could not parse uploaded data map file: {e})")

    if combined_map_parts:
        study.data_map = "\n".join(combined_map_parts)

    db.commit()


# ===========================================================================
# STEP 3 - QUOTA SHEET
# ===========================================================================
def _step3_quota(db):
    st.header("3. Quota Sheet")
    study_id = st.session_state.current_study_id

    buf = io.BytesIO()
    wb = Workbook()
    ws = wb.active
    ws.title = "Quota Sheet"
    ws.append(["quota_group", "quota_label", "column_name", "match_value", "target_n", "quota_type"])
    ws.append(["Gender", "Male", "gender", "Male", 200, "hard"])
    ws.append(["Gender", "Female", "gender", "Female", 200, "hard"])
    ws.append(["Age", "18-24", "age_band", "18-24", 100, "soft"])
    wb.save(buf)
    st.download_button("⬇ Download blank quota template", data=buf.getvalue(),
                        file_name="quota_sheet_template.xlsx")

    quota_file = st.file_uploader("Upload filled-in quota sheet (xlsx or csv)", type=["csv", "xlsx", "xls"])
    if quota_file is not None and st.button("Upload quota sheet"):
        try:
            qdf = pd.read_csv(quota_file) if quota_file.name.endswith(".csv") else pd.read_excel(quota_file)
            required = {"quota_group", "quota_label", "column_name", "match_value", "target_n"}
            missing = required - set(qdf.columns)
            if missing:
                st.error(f"Quota sheet is missing columns: {missing}")
            else:
                for _, row in qdf.iterrows():
                    dbh.add_quota_rule(
                        db, study_id, str(row["quota_group"]), str(row["quota_label"]),
                        str(row["column_name"]), str(row["match_value"]), int(row["target_n"]),
                        str(row.get("quota_type", "soft")),
                    )
                st.success(f"Added {len(qdf)} quota rules.")
                st.rerun()
        except Exception as e:
            st.error(f"Could not process file: {e}")

    st.markdown("**Or add a quota rule manually**")
    with st.form("manual_quota_form", clear_on_submit=True):
        c1, c2, c3 = st.columns(3)
        with c1:
            q_group = st.text_input("Group (e.g. Gender)")
            q_target = st.number_input("Target N", min_value=1, step=1)
        with c2:
            q_label = st.text_input("Label (e.g. Male)")
            q_type = st.selectbox("Type", ["soft", "hard"])
        with c3:
            q_column = st.text_input("Column name in data (e.g. gender)")
            q_match = st.text_input("Value to match (e.g. Male)")
        add_submitted = st.form_submit_button("+ Add Quota Rule")
    if add_submitted:
        if not (q_group.strip() and q_label.strip() and q_column.strip() and q_match.strip() and q_target):
            st.error("Please fill in every field (Group, Label, Column, Match value, and a numeric Target N) before adding.")
        else:
            dbh.add_quota_rule(db, study_id, q_group.strip(), q_label.strip(), q_column.strip(),
                                q_match.strip(), int(q_target), q_type)
            st.rerun()

    rules = dbh.list_quota_rules(db, study_id)
    if rules:
        st.markdown("**Current quota rules**")
        rules_df = pd.DataFrame([{
            "Group": r.quota_group, "Label": r.quota_label, "Column": r.column_name,
            "Match": r.match_value, "Target N": r.target_n, "Type": r.quota_type,
        } for r in rules])
        st.dataframe(rules_df, width="stretch", hide_index=True)

    st.divider()
    col_a, col_b = st.columns(2)
    with col_a:
        if st.button("← Back"):
            st.session_state.wizard_step = 2
            st.rerun()
    with col_b:
        can_finish = st.session_state.get("pending_data_file_bytes") is not None
        if st.button("Finish Setup & Run Check", type="primary", disabled=not can_finish):
            _run_check(db)
    if not can_finish:
        st.caption("⚠️ Go back to Step 2 and attach a data file before finishing setup.")


# ===========================================================================
# FINISH: run the actual QC check (mirrors the old upload_and_check route)
# ===========================================================================
def _run_check(db):
    wave_id = st.session_state.current_wave_id
    wave = dbh.get_wave(db, wave_id)
    study_id = wave.study_id
    checkpoint_pct = st.session_state.get("checkpoint_pct_pending", 0)

    os.makedirs(UPLOAD_DIR, exist_ok=True)
    timestamp = datetime.utcnow().strftime("%Y%m%d%H%M%S")
    fname = st.session_state.pending_data_file_name
    file_path = os.path.join(UPLOAD_DIR, f"wave{wave_id}_{checkpoint_pct}pct_{timestamp}_{fname}")
    with open(file_path, "wb") as f:
        f.write(st.session_state.pending_data_file_bytes)

    with st.spinner("Running QC checks and generating AI summary — this can take a minute..."):
        df = pd.read_csv(file_path) if file_path.endswith(".csv") else pd.read_excel(file_path)

        if checkpoint_pct != 0 and wave.sample_size:
            checkpoint_pct = min(100, round(100 * len(df) / wave.sample_size))

        upload = models.DataUpload(wave_id=wave_id, checkpoint_pct=checkpoint_pct, file_path=file_path)
        db.add(upload)
        db.commit()
        db.refresh(upload)

        config = _build_config_from_session()

        rules = dbh.list_quota_rules(db, study_id)
        config["quota_rules"] = [{
            "quota_group": r.quota_group, "quota_label": r.quota_label,
            "column_name": r.column_name, "match_value": r.match_value,
            "target_n": r.target_n, "quota_type": r.quota_type,
        } for r in rules]

        previous_df = None
        previous_topline = None
        previous_wave_label = "the previous wave"
        other_waves = db.query(models.Wave).filter(
            models.Wave.study_id == study_id, models.Wave.id != wave_id
        ).order_by(models.Wave.wave_number.desc()).all()
        if other_waves:
            prev_wave = other_waves[0]
            prev_upload = db.query(models.DataUpload).filter(
                models.DataUpload.wave_id == prev_wave.id
            ).order_by(models.DataUpload.checkpoint_pct.desc()).first()
            if prev_upload:
                previous_df = (pd.read_csv(prev_upload.file_path) if prev_upload.file_path.endswith(".csv")
                               else pd.read_excel(prev_upload.file_path))
                prev_check_result = db.query(models.CheckResult).filter(
                    models.CheckResult.upload_id == prev_upload.id
                ).first()
                if prev_check_result:
                    previous_topline = prev_check_result.result_json.get("topline", {})
                previous_wave_label = prev_wave.display_name

        # Plain-English custom QC rules need AI to interpret, which is
        # currently disabled - leave them unevaluated (report_builder marks
        # them clearly rather than silently dropping them). Condition
        # Builder rules don't need AI and still run fully in checks.py.
        results = qc.run_all_checks(df, config, checkpoint_pct, previous_wave_df=previous_df)

        study = dbh.get_study(db, study_id)
        summary = report_builder.build_summary_report(
            results, study.data_map or "", study.name, checkpoint_pct,
            previous_topline=previous_topline,
            current_wave_label=wave.display_name,
            previous_wave_label=previous_wave_label,
        )
        email_draft = report_builder.build_email_draft(study.name, checkpoint_pct, results["issues"])

        check_result = models.CheckResult(
            upload_id=upload.id, result_json=results,
            ai_summary=summary, ai_email_draft=email_draft,
        )
        db.add(check_result)
        db.commit()

    for k in ("pending_data_file_bytes", "pending_data_file_name"):
        st.session_state.pop(k, None)

    st.session_state.page = "dashboard"
    st.session_state.dashboard_stage = "soft" if checkpoint_pct == 0 else "full"
    st.success("Check complete!")
    st.rerun()


def _build_config_from_session() -> dict:
    s = st.session_state

    grid_column_groups = {}
    for row in _df_records(s.get("cfg_grid_groups")):
        name = str(row.get("grid_name") or "").strip()
        cols_raw = str(row.get("columns_comma_separated") or "")
        cols = [c.strip() for c in cols_raw.split(",") if c.strip()]
        if name and cols:
            grid_column_groups[name] = cols

    consistency_rules = []
    for row in _df_records(s.get("cfg_consistency_rules")):
        if_col = str(row.get("if_col") or "").strip()
        then_col = str(row.get("then_col") or "").strip()
        if if_col and then_col:
            invalid_vals = [v.strip() for v in str(row.get("then_invalid_values_comma_sep") or "").split(",") if v.strip()]
            consistency_rules.append({
                "label": str(row.get("label") or f"{if_col} vs {then_col}"),
                "if_col": if_col, "if_value": str(row.get("if_value") or ""),
                "then_col": then_col, "then_invalid_values": invalid_vals,
            })

    numeric_ranges = {}
    for row in _df_records(s.get("cfg_numeric_ranges")):
        col = str(row.get("column") or "").strip()
        if col and row.get("min") is not None and row.get("max") is not None:
            try:
                numeric_ranges[col] = [float(row["min"]), float(row["max"])]
            except (ValueError, TypeError):
                pass

    skip_rules = []
    for row in _df_records(s.get("cfg_skip_rules")):
        skip_col = str(row.get("skip_if_col") or "").strip()
        target_col = str(row.get("should_be_blank_col") or "").strip()
        if skip_col and target_col:
            skip_rules.append({
                "label": str(row.get("label") or f"{skip_col} -> {target_col}"),
                "skip_if_col": skip_col, "skip_if_value": str(row.get("skip_if_value") or ""),
                "should_be_blank_col": target_col,
            })

    attention_rules = []
    for row in _df_records(s.get("cfg_attention_rules")):
        col = str(row.get("column") or "").strip()
        if col:
            attention_rules.append({
                "label": str(row.get("label") or f"Attention check: {col}"),
                "column": col, "expected_value": str(row.get("expected_value") or ""),
            })

    # Custom QC: condition rules, grouped by rule_label
    custom_rules = []
    grouped: dict[str, dict] = {}
    for row in _df_records(s.get("cfg_custom_condition_rules")):
        label = str(row.get("rule_label") or "").strip()
        col = str(row.get("column") or "").strip()
        if not label or not col:
            continue
        if label not in grouped:
            grouped[label] = {"type": "conditional", "label": label,
                               "join": str(row.get("join") or "AND").upper(), "conditions": []}
        grouped[label]["conditions"].append({
            "column": col, "operator": str(row.get("operator") or "equals"),
            "value": row.get("value", ""),
        })
    custom_rules.extend(grouped.values())

    for row in _df_records(s.get("cfg_custom_plain_rules")):
        desc = str(row.get("description") or "").strip()
        if desc:
            custom_rules.append({
                "type": "plain_english",
                "label": str(row.get("label") or "Custom Rule"),
                "description": desc,
            })

    distribution_cols = _lines_to_list(s.get("cfg_question_columns_text", "")) + \
        _lines_to_list(s.get("cfg_distribution_text", ""))

    return {
        "question_columns": _lines_to_list(s.get("cfg_question_columns_text", "")),
        "grid_column_groups": grid_column_groups,
        "duration_col": s.get("cfg_duration_col") or "duration_seconds",
        "wave_compare_columns": _lines_to_list(s.get("cfg_wave_compare_text", "")),
        "id_columns": _lines_to_list(s.get("cfg_id_columns_text", "")),
        "open_end_columns": _lines_to_list(s.get("cfg_open_end_text", "")),
        "consistency_rules": consistency_rules,
        "numeric_ranges": numeric_ranges,
        "skip_rules": skip_rules,
        "distribution_columns": distribution_cols,
        "attention_rules": attention_rules,
        "custom_rules": custom_rules,
    }
