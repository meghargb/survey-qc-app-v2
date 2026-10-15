# Survey Data QC Tool — Streamlit Edition

Same tool, same features (12 core QC checks, Custom QC Rules, Data Map,
quota tracking, multi-wave comparison, exports to xlsx/csv/docx/pptx/sav),
running as a single Streamlit app instead of a FastAPI backend + separate
frontend.

**AI features are currently disabled** (Ask AI, plain-English Custom QC
Rules, and the old AI-written summary/email). Instead, `report_builder.py`
generates the "QC & Topline Report" and vendor email draft **deterministically
from your data map** — no network calls, nothing that needs an API key.
It walks every QC check result and the topline data, translates every
column code into its plain-English label using your data map (e.g.
`V1_T2Br9` → "Awareness - Jeep (Top-2-Box): 81%"), and writes it out in
plain sentences. Condition Builder custom rules still run fully; plain-
English rules are clearly marked "not evaluated" since they need AI.

**Wave-over-wave comparison** is automatic: whenever you add a new wave to
an existing project, the report compares every data-map-labeled metric
against the most recent previous wave and writes a plain-English summary,
e.g. "Wave 2 data indicates that Sponsorship Awareness - Brand XYZ is 64%
(+8 pts vs Wave 1)." Waves can be numbered (Wave 1, Wave 2...) or given a
custom name (Pre-Wave, Post-Wave, Baseline, Follow-up...) in Step 1 of the
wizard - either style works the same way.

To bring AI back later: `ai_helper.py` is still in the project, just not
imported. Re-enable it in `ui_wizard.py` (swap `report_builder.build_*`
calls back to `ai_helper.generate_insight_summary` / `draft_warning_email`)
and add the Ask AI panel back to `ui_dashboard.py`, then set
`ANTHROPIC_API_KEY` in secrets.

## Deploying to Streamlit Cloud

1. Push this folder to a GitHub repo.
2. Go to your Streamlit Cloud dashboard → **New app** → point it at your
   repo/branch, main file `app.py`.
3. Deploy. No secrets are required for login - each person creates their
   own account (email + a password they choose) right in the app, on the
   "Create Account" tab. Everyone then shares the same workspace of
   projects; accounts just gate who can get in.

## Running locally

```bash
pip install -r requirements.txt
streamlit run app.py
```

## File structure

```
app.py                          # entry point: login/sign-up, session state, page routing
auth.py                         # password hashing + email/account helpers
ui_wizard.py                    # Setup Wizard: Step 1 (Project) → 2 (Questionnaire/Data
                                 # Map/Advanced checks) → 3 (Quota) → run the check
ui_dashboard.py                 # Soft Launch / Full Launch dashboard tabs
report_builder.py               # deterministic (no-AI) report + email generator,
                                 # driven entirely by your data map
db_helpers.py                   # small query helpers wrapping the SQLAlchemy models
models.py, database.py          # plain SQLAlchemy, unchanged from the original app
checks.py                       # unchanged - all 12 QC checks + custom QC + topline
datamap_parser.py               # unchanged - parses Decipher-style hierarchical datamaps
ai_helper.py, exporters.py      # ai_helper unused for now (see above); exporters unchanged
```

## ⚠️ Accounts live in the same database as everything else

Since accounts (email + password) are stored in the same SQLite database
as projects and check results, they're subject to the same platform
limitation: if your hosting wipes local storage on restart/redeploy,
**everyone's accounts get wiped too**, not just project data - everyone
would need to sign up again. If that becomes a problem, moving to a
hosted Postgres database (e.g. Supabase, Neon) fixes it for both accounts
and projects at once - ask if you want help setting that up.

