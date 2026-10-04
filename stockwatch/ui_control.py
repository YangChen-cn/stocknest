"""Cloud, optional Gmail and macOS controls separated from portfolio widgets."""
from datetime import datetime
from zoneinfo import ZoneInfo
import streamlit as st

from stockwatch.control import (ControlError, cloud_email_status, workflow_status, set_workflow_enabled,
                               trigger_workflow, service_status, install_service, stop_service, uninstall_service)
from stockwatch.git_sync import SyncError, sync
from stockwatch.i18n import t, error_message
from stockwatch.notifications.local import credentials, load_local, save_local, remove_local
from stockwatch.storage import ValidationError


def text(message, **values):
    return t(message, st.session_state.get('_stockwatch_language', 'en'), **values)


def localized_error(error):
    return error_message(error, st.session_state.get('_stockwatch_language', 'en'))


def gmail_controls(demo, ROOT, readonly=False):
    st.caption(text("Cloud Gmail uses GitHub Actions Secrets. Local Gmail is optional and does not indicate cloud health."))
    if st.button(text("Check cloud Gmail Secrets"), disabled=demo):
        try:
            st.session_state["_cloud_gmail_status"] = cloud_email_status(ROOT)
        except ControlError:
            st.session_state.pop("_cloud_gmail_status", None)
            st.info(text("Cloud Secrets could not be checked. Use GitHub repository settings; local Gmail is independent."))
    cloud = st.session_state.get("_cloud_gmail_status") if not demo else None
    if cloud is not None:
        st.write(text("Cloud Gmail Secrets") + ": " + ", ".join(f"{name}: {text('Configured' if ready else 'Missing')}" for name, ready in cloud.items()))
    with st.expander(text("Advanced: optional local Gmail"), expanded=False):
        st.caption(text("Used only for local email and direct HSBC sync. Saved as a plaintext file readable only by your user, ignored by Git; it never updates cloud Secrets."))
        st.caption(text("Environment variables take precedence. Leave App Password blank to retain the saved local password; it is never displayed."))
        try:
            values = {} if demo else credentials(ROOT)
            local = {} if demo else load_local(ROOT)
            status = {name: bool(value) for name, value in values.items()}
            st.write(text("Local Gmail ready" if status and all(status.values()) else "Local Gmail not configured (optional)"))
            if st.session_state.pop("_clear_local_password", False):
                st.session_state["local_gmail_password"] = ""
            with st.form("local_gmail"):
                address = st.text_input(text("Local Gmail Address"), value=local.get("GMAIL_ADDRESS", values.get("GMAIL_ADDRESS", "")))
                recipient = st.text_input(text("Local Report Email"), value=local.get("REPORT_EMAIL", values.get("REPORT_EMAIL", "")))
                password = st.text_input(text("Local Gmail App Password"), type="password", key="local_gmail_password")
                saved = st.form_submit_button(text("Save local Gmail"), disabled=demo or readonly)
            if saved:
                save_local(address, recipient, password, ROOT)
                st.session_state["_clear_local_password"] = True
                st.session_state["_stockwatch_notice"] = "Local Gmail saved. Cloud Secrets were not changed."
                st.rerun()
            if st.button(text("Remove saved local Gmail"), disabled=demo or readonly or not local):
                remove_local(ROOT)
                st.session_state["_clear_local_password"] = True
                st.session_state["_stockwatch_notice"] = "Local Gmail removed. Environment variables and cloud Secrets were not changed."
                st.rerun()
        except (ValidationError, OSError) as exc:
            st.error(localized_error(exc) if not isinstance(exc, OSError) else text("Save failed; original file preserved."))

def control_center(path, config, demo, ROOT, hsbc_control, email_control, readonly=False):
    st.subheader(text("Control Center"))
    with st.expander(text("HSBC trade import")):
        hsbc_control(path, config, demo, settings=True)
    st.caption(text("Scheduled reports run in GitHub Actions, even when this computer is off. These controls manage the existing cloud workflow."))
    with st.expander(text("Cloud automation (GitHub Actions)"), expanded=False):
        email_control(path, config, demo)
        if st.button(text("Sync notification setting with GitHub"), disabled=demo or readonly):
            try:
                sync(ROOT)
                st.success(text("Configuration synced to GitHub."))
            except (SyncError, ValidationError) as exc:
                st.error(localized_error(exc))
        st.markdown(f"**{text('GitHub Actions')}**")
        if st.button(text("Refresh workflow status"), disabled=demo):
            try:
                st.session_state["_workflow_status"] = workflow_status(ROOT)
            except ControlError as exc:
                st.error(localized_error(exc))
        status = st.session_state.get("_workflow_status") if not demo else None
        if status:
            st.write(f"{status['repository']} · {text(status['state'])}")
            st.link_button(text("Open workflow"), status["url"])
            for run in status["runs"]:
                stamp = datetime.fromisoformat(run["created_at"].replace("Z", "+00:00")).astimezone(ZoneInfo("Asia/Hong_Kong")).strftime("%m-%d %H:%M HKT")
                label = f"{stamp} · {text(run['status'])} · {text(run['conclusion'] or 'Pending')}"
                st.link_button(label, run["html_url"])
        with st.form("workflow_controls"):
            mode_labels = {"CLOSE": text("Closing report"), "INTRADAY": text("Intraday brief"), "WEEKLY": text("Weekly report"), "MONTHLY": text("Monthly report")}
            mode = st.selectbox(text("Report mode"), list(mode_labels), format_func=mode_labels.__getitem__)
            dry_run = st.checkbox(text("Preview only (no email or state changes)"), value=True)
            dispatch = st.form_submit_button(text("Run workflow"), disabled=demo or readonly)
        if dispatch:
            try:
                trigger_workflow(ROOT, mode, dry_run=dry_run)
                st.success(text("Workflow queued. Refresh status to see its result."))
            except ControlError as exc:
                st.error(localized_error(exc))
        left, right = st.columns(2)
        enable = left.button(text("Enable cloud daily workflow"), disabled=demo or readonly)
        disable = right.button(text("Disable cloud daily workflow"), disabled=demo or readonly)
        if enable or disable:
            try:
                set_workflow_enabled(ROOT, enable)
                st.session_state["_workflow_status"] = workflow_status(ROOT)
                st.success(text("Workflow setting updated."))
            except ControlError as exc:
                st.error(localized_error(exc))
        st.caption(text("Uses your existing gh login. Disabling the daily workflow stops both schedules; CI stays enabled."))
    with st.expander(text("macOS login startup"), expanded=False):
        try:
            local = service_status(ROOT)
            st.write(text("Service: {state} · Login startup: {startup} · Port 8501: {port}",
                          state=text("Running" if local["running"] else "Manual Dashboard or other process" if local["port_open"] else "Stopped"),
                          startup=text("Enabled" if local["installed"] else "Disabled"),
                          port=text("Responding" if local["port_open"] else "Closed")))
            if local["supported"]:
                cols = st.columns(3)
                actions = ("Enable login startup", "Stop service", "Disable login startup")
                funcs = (install_service, stop_service, uninstall_service)
                for column, label, function in zip(cols, actions, funcs):
                    if column.button(text(label), disabled=demo or label == "Stop service" and not local["loaded"]):
                        function(ROOT)
                        st.success(text("Service setting updated. Refresh the page to see its status."))
            else:
                st.info(text("Login startup is available only on macOS."))
        except ControlError as exc:
            st.error(localized_error(exc))
        st.caption(text("Enabling login startup only registers the next login and leaves this Dashboard running. To switch now, stop the manual terminal process, then run python -m stockwatch.control start in that terminal."))
        st.caption(text("Login startup runs only the local Dashboard on 127.0.0.1:8501, not email jobs. Stopping it disconnects this page; cloud schedules continue."))
