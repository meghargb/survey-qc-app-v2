"""
Survey Data QC Tool - Streamlit edition.

Same business logic as the original FastAPI + HTML/JS version (checks.py,
ai_helper.py, exporters.py, datamap_parser.py, models.py are reused
unchanged) - this file and ui_wizard.py / ui_dashboard.py replace the old
main.py routes + index.html/app.js frontend with Streamlit's UI model.
"""
import os

import streamlit as st

# Bridge Streamlit secrets (Streamlit Cloud's "Secrets" panel) into
# environment variables, so ai_helper.py - which reads plain os.getenv()
# calls, unchanged from the original app - picks them up automatically
# without needing any code changes there.
for _key in ("ANTHROPIC_API_KEY", "GEMINI_API_KEY", "AI_PROVIDER"):
    try:
        if _key in st.secrets:
            os.environ[_key] = str(st.secrets[_key])
    except Exception:
        pass  # no secrets.toml configured locally - fine, .env / os.environ still works

from database import Base, engine, SessionLocal
import models  # noqa: F401  (needed so Base.metadata knows about the tables)
import auth
import ui_wizard
import ui_dashboard

st.set_page_config(page_title="Survey Data QC Tool", page_icon="📊", layout="wide")

Base.metadata.create_all(bind=engine)

UPLOAD_DIR = "uploaded_files"
os.makedirs(UPLOAD_DIR, exist_ok=True)


# ---------------------------------------------------------------------------
# LOGIN / SIGN UP - everyone creates their own account (email + a password
# they choose) and logs in individually. Everyone then shares the same
# workspace of projects - accounts just gate who can get in, they don't
# split up the data.
# ---------------------------------------------------------------------------
def _login_signup_screen(db):
    st.title("📊 Survey Data QC Tool")

    tab_login, tab_signup = st.tabs(["Log In", "Create Account"])

    with tab_login:
        with st.form("login_form"):
            email = st.text_input("Email", key="login_email")
            password = st.text_input("Password", type="password", key="login_password")
            submitted = st.form_submit_button("Log In", type="primary")
        if submitted:
            if not email or not password:
                st.error("Enter both your email and password.")
            else:
                user = auth.authenticate(db, email, password)
                if user:
                    st.session_state.authed = True
                    st.session_state.user_email = user.email
                    st.rerun()
                else:
                    st.error("Incorrect email or password.")

    with tab_signup:
        with st.form("signup_form"):
            new_email = st.text_input("Email", key="signup_email")
            new_password = st.text_input("Choose a password", type="password", key="signup_password")
            confirm_password = st.text_input("Confirm password", type="password", key="signup_confirm")
            st.caption("Password must be at least 8 characters and include a letter and a number.")
            signup_submitted = st.form_submit_button("Create Account", type="primary")
        if signup_submitted:
            if not new_email or not new_password or not confirm_password:
                st.error("Fill in every field.")
            elif not auth.is_valid_email(new_email):
                st.error("That doesn't look like a valid email address.")
            elif auth.get_user_by_email(db, new_email):
                st.error("An account with that email already exists - log in instead.")
            elif new_password != confirm_password:
                st.error("Passwords don't match.")
            else:
                issues = auth.password_issues(new_password)
                if issues:
                    for i in issues:
                        st.error(i)
                else:
                    user = auth.create_user(db, new_email, new_password)
                    st.session_state.authed = True
                    st.session_state.user_email = user.email
                    st.success("Account created!")
                    st.rerun()


def check_login(db) -> bool:
    if st.session_state.get("authed"):
        return True
    _login_signup_screen(db)
    return False


# ---------------------------------------------------------------------------
# SESSION STATE DEFAULTS
# ---------------------------------------------------------------------------
defaults = {
    "authed": False,
    "user_email": None,
    "page": "wizard",          # "wizard" | "dashboard"
    "wizard_step": 1,          # 1 | 2 | 3
    "project_mode": "new",     # "new" | "existing"
    "wave_mode": "new",        # "new" | "fetch"  (only relevant if project_mode == "existing")
    "current_study_id": None,
    "current_wave_id": None,
    "just_ran_checkpoint_pct": None,
}
for k, v in defaults.items():
    if k not in st.session_state:
        st.session_state[k] = v


# ---------------------------------------------------------------------------
# ROUTER
# ---------------------------------------------------------------------------
db = SessionLocal()
try:
    if not check_login(db):
        st.stop()

    st.sidebar.title("📊 Survey Data QC Tool")
    st.sidebar.caption(f"Logged in as **{st.session_state.user_email}**")
    if st.sidebar.button("Log out"):
        st.session_state.authed = False
        st.session_state.user_email = None
        st.rerun()

    if st.session_state.current_study_id:
        from db_helpers import get_study
        study = get_study(db, st.session_state.current_study_id)
        if study:
            st.sidebar.caption(f"Project: **{study.name}**")

    nav = st.sidebar.radio(
        "Navigate",
        ["Setup Wizard", "Dashboard"],
        index=0 if st.session_state.page == "wizard" else 1,
        label_visibility="collapsed",
    )
    st.session_state.page = "wizard" if nav == "Setup Wizard" else "dashboard"

    if st.session_state.page == "wizard":
        ui_wizard.render(db)
    else:
        ui_dashboard.render(db)
finally:
    db.close()
