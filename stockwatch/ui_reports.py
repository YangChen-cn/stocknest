"""Report configuration widgets, kept separate from portfolio rendering."""
from datetime import time

import streamlit as st

from stockwatch.control import ControlError, apply_report_schedules
from stockwatch.git_sync import SyncError, sync
from stockwatch.i18n import error_message, t
from stockwatch.report_settings import MODES, PERIODIC, report_settings
from stockwatch.storage import ValidationError, load_config, save_config
from stockwatch import ui_cloud

LABELS = {"INTRADAY": "Intraday brief", "CLOSE": "Closing report", "WEEKLY": "Weekly report", "MONTHLY": "Monthly report"}
DAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday")
TRIGGERS = ("native", "cron-job.org")


def save_trigger(path, current, desired, *, readonly):
    """Local save, or a direct repository write when cloud editing is enabled."""
    if readonly:
        from stockwatch.i18n import t as translate
        language = st.session_state.get('_stockwatch_language', 'en')
        try:
            result = ui_cloud.CloudRepository(ui_cloud.CloudSettings.from_environment()).save_scheduler_trigger(
                current.get("scheduler", {}).get("trigger", "native"), desired)
        except ui_cloud.CloudConfigError as exc:
            st.error(error_message(exc, language))
            return
        st.session_state['_report_settings_signature'] = None
        message = ("Schedule trigger saved to GitHub; the next scheduled run uses it."
                   if result["changed"] else "No new changes. GitHub already has this trigger.")
        st.success(translate(message, language))
        return
    try:
        latest = load_config(path)
        if desired == "native":
            latest.pop("scheduler", None)
        else:
            latest["scheduler"] = {"trigger": desired}
        save_config(path, latest)
        st.session_state['_stockwatch_notice'] = "Schedule trigger saved locally. Sync with GitHub to apply it to scheduled reports."
        st.rerun()
    except (ValidationError, OSError) as exc:
        st.error(error_message(exc, st.session_state.get('_stockwatch_language', 'en')))


def report_controls(path, config, demo, root, readonly=False):
    lang = st.session_state.get('_stockwatch_language', 'en')
    def tr(message):
        return t(message, lang)
    with st.expander(tr("Report schedules"), expanded=False):
        st.caption(tr("Daily times use New York time with automatic DST; weekly/monthly times use Hong Kong time."))
        settings = report_settings(config.get('reports'))
        current_trigger = config.get('scheduler', {}).get('trigger', 'native')
        signature = (str(settings), demo, current_trigger)
        if st.session_state.get('_report_settings_signature') != signature:
            for mode in MODES:
                for field in ('enabled', 'time', 'days'):
                    st.session_state.pop(f'report_{mode}_{field}', None)
            st.session_state.pop('scheduler_trigger', None)
            st.session_state['_report_settings_signature'] = signature
        trigger_labels = {"native": tr("GitHub Actions schedule"), "cron-job.org": tr("External cron-job.org triggers")}
        trigger = st.selectbox(tr("Schedule trigger"), list(TRIGGERS), index=TRIGGERS.index(current_trigger),
                               format_func=trigger_labels.__getitem__, key="scheduler_trigger",
                               help=tr("While external triggers are selected, GitHub's native schedule slots are skipped. Switch back if the external jobs are removed."))
        trigger_col, _ = st.columns([1, 2])
        if trigger_col.button(tr("Save schedule trigger"), disabled=demo or (readonly and not ui_cloud.editing_ready()),
                              key="save_scheduler_trigger"):
            save_trigger(path, config, trigger, readonly=readonly)
        with st.form('report_schedules'):
            updated = {}
            for mode in MODES:
                item = settings[mode]
                cols = st.columns([2, 2, 3])
                enabled = cols[0].checkbox(tr(LABELS[mode]), value=item['enabled'], key=f'report_{mode}_enabled')
                hour, minute = map(int, item['time'].split(':'))
                clock = cols[1].time_input(tr('Time (Hong Kong)' if mode in PERIODIC else 'Time (New York)'), value=time(hour, minute), step=60, key=f'report_{mode}_time')
                updated[mode] = {'enabled': enabled, 'time': clock.strftime('%H:%M')}
                if mode in PERIODIC:
                    cols[2].caption(tr('Every Saturday · Hong Kong time' if mode == 'WEEKLY' else 'First weekend day of each month · previous month'))
                else:
                    updated[mode]['days'] = cols[2].multiselect(tr('Weekdays'), list(range(5)), default=item['days'], format_func=lambda day: tr(DAYS[day]), key=f'report_{mode}_days')
            st.caption(tr("Weekend summaries use completed closes: weekly through the last session of the week, monthly through the last session of the previous month. Default 10:52 Hong Kong time."))
            saved = st.form_submit_button(tr('Save report schedules'), disabled=demo or readonly)
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
        if st.button(tr('Sync and apply cloud schedules'), disabled=demo or readonly):
            try:
                sync(root)
                apply_report_schedules(root)
                st.success(tr('Scheduler update queued. Check the Apply report schedules workflow result; saving locally alone does not change external trigger times.'))
            except (ControlError, SyncError, ValidationError) as exc:
                st.error(error_message(exc, lang))
