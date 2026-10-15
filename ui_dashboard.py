"""
Dashboard: Soft Launch / Full Launch tabs. Mirrors the old dashboard.html
+ /waves/{id}/checkpoint_result, /waves/{id}/ask, /waves/{id}/export routes -
each tab always re-fetches the real, persisted result for that specific
stage (never a stale render left over from a different checkpoint).
"""
import io
import streamlit as st
import pandas as pd

import models
import exporters
import db_helpers as dbh


def render(db):
    if not st.session_state.current_wave_id:
        st.info("No project set up yet. Go to the Setup Wizard to create or select a project.")
        return

    wave = dbh.get_wave(db, st.session_state.current_wave_id)
    study = dbh.get_study(db, wave.study_id)

    top_l, top_r = st.columns([4, 1])
    with top_l:
        st.title(f"📊 {study.name}")
        st.caption(wave.display_name)
    with top_r:
        if st.button("+ New Checkpoint / Setup"):
            st.session_state.page = "wizard"
            st.session_state.wizard_step = 1
            st.rerun()

    default_stage = st.session_state.get("dashboard_stage", "full")
    tab_soft, tab_full = st.tabs(["Soft Launch", "Full Launch"])

    with tab_soft:
        _render_stage(db, wave, study, "soft")
    with tab_full:
        _render_stage(db, wave, study, "full")


def _render_stage(db, wave, study, stage: str):
    upload = dbh.get_latest_upload_for_stage(db, wave.id, stage)
    if not upload:
        label = "Soft Launch" if stage == "soft" else "Full Launch"
        st.info(f"No {label} checkpoint has been uploaded yet. "
                f"Use \"+ New Checkpoint / Setup\" above to upload one.")
        return

    check_result = db.query(models.CheckResult).filter(
        models.CheckResult.upload_id == upload.id
    ).first()
    if not check_result:
        st.warning("That checkpoint's results are still processing.")
        return

    results = check_result.result_json

    if stage == "full":
        st.subheader(f"Full Launch — {upload.checkpoint_pct}% Checkpoint")
        history = dbh.wave_history(db, wave.id)
        full_history = sorted([h for h in history if h["checkpoint_pct"] > 0], key=lambda h: h["checkpoint_pct"])
        if len(full_history) > 1:
            trail = " → ".join(f"{h['checkpoint_pct']}%" for h in full_history)
            st.caption(f"Checkpoint history: {trail}")
    else:
        st.subheader("Soft Launch")

    st.caption(f"Total respondents: {results.get('total_respondents', '—')}")

    # --- Issues ---
    st.markdown("#### QC Issues")
    issues = results.get("issues", [])
    if not issues:
        st.success("All questions answering correctly, no logic issues found ✅")
    else:
        for issue in issues:
            st.warning(issue)

    # --- Quota status ---
    quota = results.get("quota", {})
    if quota.get("details"):
        st.markdown("#### Quota Status")
        qdf = pd.DataFrame(quota["details"])
        st.dataframe(qdf, width="stretch", hide_index=True)

    # --- Custom QC results ---
    custom_qc = results.get("custom_qc", {})
    if custom_qc.get("rules_evaluated", 0) > 0:
        st.markdown("#### Custom QC Rules")
        for r in custom_qc.get("rule_results", []):
            method = "AI" if r["type"] == "plain_english" else "Condition"
            if r["flagged_count"] > 0:
                rows_str = ", ".join(str(x) for x in r["flagged_rows"])
                st.warning(f"**{r['label']}** ({method}): {r['flagged_count']} flagged — rows: {rows_str}")
            else:
                st.success(f"**{r['label']}** ({method}): 0 flagged ✅")

    if stage == "full":
        st.markdown("#### QC & Topline Report")
        st.caption("Generated directly from your data and data map — no AI involved.")
        if check_result.ai_summary:
            st.markdown(check_result.ai_summary)
        else:
            st.caption("No report generated.")

        st.markdown("#### Vendor Email Draft")
        st.text_area("Vendor Email Draft", value=check_result.ai_email_draft or "No issues detected at this checkpoint - no email needed.",
                     height=180, key=f"email_draft_{upload.id}", label_visibility="collapsed")

        st.caption("💡 Ask AI and plain-English Custom QC Rules are currently disabled. "
                   "Condition Builder rules still run fully without AI.")

        st.markdown("#### Export")
        fmt = st.selectbox("Format", ["xlsx", "csv", "docx", "pptx", "sav"], key=f"export_fmt_{wave.id}")
        data, media, fname = _export(fmt, study, upload, check_result)
        if data is not None:
            st.download_button(f"⬇ Download .{fmt}", data=data, file_name=fname, mime=media,
                                key=f"export_btn_{wave.id}")

    with st.expander("Raw results (JSON)"):
        st.json(results)


def _export(fmt: str, study, upload, check_result):
    results = check_result.result_json
    summary = check_result.ai_summary or ""
    email_draft = check_result.ai_email_draft or ""
    try:
        if fmt == "xlsx":
            return (exporters.export_xlsx(study.name, results, summary, email_draft),
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    f"{study.name}_QC_report.xlsx")
        elif fmt == "csv":
            return (exporters.export_csv(study.name, results, summary, email_draft),
                    "text/csv", f"{study.name}_QC_report.csv")
        elif fmt == "docx":
            return (exporters.export_docx(study.name, results, summary, email_draft),
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    f"{study.name}_QC_report.docx")
        elif fmt == "pptx":
            return (exporters.export_pptx(study.name, results, summary, email_draft),
                    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
                    f"{study.name}_QC_report.pptx")
        elif fmt == "sav":
            df = pd.read_csv(upload.file_path) if upload.file_path.endswith(".csv") else pd.read_excel(upload.file_path)
            return (exporters.export_sav(study.name, df), "application/octet-stream", f"{study.name}_data.sav")
    except Exception as e:
        st.error(f"Export failed: {e}")
    return None, None, None
