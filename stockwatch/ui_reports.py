"""Report configuration widgets, kept separate from portfolio rendering."""
from datetime import time

import streamlit as st

from stockwatch.control import ControlError, apply_report_schedules
from stockwatch.git_sync import SyncError, sync
from stockwatch.i18n import error_message, t
from stockwatch.report_settings import MODES, report_settings
from stockwatch.storage import ValidationError, load_config, save_config

LABELS = {"INTRADAY": "Intraday brief", "CLOSE": "Closing report", "WEEKLY": "Weekly report", "MONTHLY": "Monthly report"}
DAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday")


def report_controls(path, config, demo, root):
    lang = st.session_state.get('_stockwatch_language', 'en')
    def tr(message):
        return t(message, lang)
    with st.expander(tr("Report schedules"), expanded=False):
        st.caption(tr("All times are New York time (automatic DST). Daily reports skip holidays. Weekly is week-to-date; monthly covers the previous calendar month."))
        settings = report_settings(config.get('reports'))
        signature = (str(settings), demo)
        if st.session_state.get('_report_settings_signature') != signature:
            for mode in MODES:
                for field in ('enabled', 'time', 'days'):
                    st.session_state.pop(f'report_{mode}_{field}', None)
            st.session_state['_report_settings_signature'] = signature
        with st.form('report_schedules'):
            updated = {}
            for mode in MODES:
                item = settings[mode]
                cols = st.columns([2, 2, 3])
                enabled = cols[0].checkbox(tr(LABELS[mode]), value=item['enabled'], key=f'report_{mode}_enabled')
                hour, minute = map(int, item['time'].split(':'))
                clock = cols[1].time_input(tr('Time (New York)'), value=time(hour, minute), step=60, key=f'report_{mode}_time')
                if mode == 'WEEKLY':
                    days = [cols[2].selectbox(tr('Weekday'), list(range(5)), index=item['days'][0], format_func=lambda day: tr(DAYS[day]), key=f'report_{mode}_days')]
                else:
                    days = cols[2].multiselect(tr('Weekdays'), list(range(5)), default=item['days'], format_func=lambda day: tr(DAYS[day]), key=f'report_{mode}_days')
                updated[mode] = {'enabled': enabled, 'time': clock.strftime('%H:%M'), 'days': days}
            st.caption(tr("Monthly sends on the first NYSE session matching your weekdays in the new month. Weekly holidays are skipped. Manual runs can generate missed summaries."))
            saved = st.form_submit_button(tr('Save report schedules'), disabled=demo)
        if saved:
            try:
                current = load_config(path)
                current['reports'] = updated
                save_config(path, current)
                st.session_state['_stockwatch_notice'] = 'Report schedules saved locally. Apply cloud schedules to change trigger times.'
                st.rerun()
            except (ValidationError, OSError) as exc:
                st.error(error_message(exc, lang) if isinstance(exc, ValidationError) else tr('Save failed; original file preserved.'))
        st.caption(tr("Cloud time changes require CRONJOB_API_KEY in GitHub Secrets and an existing authorized cron-job.org report job. The key is never saved in this app. Native GitHub schedules remain a fixed-time fallback."))
        if st.button(tr('Sync and apply cloud schedules'), disabled=demo):
            try:
                sync(root)
                apply_report_schedules(root)
                st.success(tr('Scheduler update queued. Check the Apply report schedules workflow result; saving locally alone does not change external trigger times.'))
            except (ControlError, SyncError, ValidationError) as exc:
                st.error(error_message(exc, lang))
