"""Small Streamlit bridge for optional cloud watchlist/notes saves."""
from copy import deepcopy

import streamlit as st

from stockwatch.cloud_config import CloudRepository, CloudSettings, configured, merge_watchlist
from stockwatch.i18n import t
from stockwatch.storage import load_config, save_config


def text(message):
    return t(message, st.session_state.get('_stockwatch_language', 'en'))


@st.cache_data(ttl=30, show_spinner=False)
def cloud_config(repository):
    # The token is never a cache argument or part of a returned value.
    return CloudRepository(CloudSettings.from_environment()).read_config()['config']


def editing_ready():
    return configured() and st.session_state.get('_cloud_edit_ready', False)


def reset_editors():
    st.session_state.pop('_watch_edit_bases', None)
    for key in list(st.session_state):
        if key.startswith(('thesis_', 'status_', 'buy_below_', 'target_', 'below_', 'move_', 'holding_thesis_', 'holding_buy_', 'holding_target_', 'holding_below_', 'holding_move_')) or key == 'watchlist_editor_False':
            del st.session_state[key]


def edit_base(key, config):
    # Drafts retain the exact base they were opened against, even if the remote
    # read cache expires. Refreshing explicitly discards them with a clear label.
    bases = st.session_state.setdefault('_watch_edit_bases', {})
    return bases.setdefault(key, deepcopy(config))


def save_watchlist(path, base, desired, *, hosted):
    if hosted:
        result = CloudRepository(CloudSettings.from_environment()).save_watchlist(base['watchlist'], desired['watchlist'])
        cloud_config.clear()
        st.session_state['_reset_watch_editors'] = True
        return 'Watchlist saved to GitHub. Click Sync with GitHub on your local Dashboard to pull it.' if result['changed'] else 'No new changes. GitHub already has this watchlist.'
    latest = load_config(path)
    latest['watchlist'] = merge_watchlist(base['watchlist'], desired['watchlist'], latest['watchlist'])
    save_config(path, latest)
    st.session_state['_reset_watch_editors'] = True
    return 'Watchlist saved locally. Sync with GitHub to update daily alerts.'
