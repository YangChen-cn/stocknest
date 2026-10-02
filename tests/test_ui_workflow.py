import pytest
import yaml
from streamlit.testing.v1 import AppTest

from stockwatch.storage import ROOT, load_config, load_transactions, save_config, save_transactions
from stockwatch.i18n import t


@pytest.fixture
def app(tmp_path, monkeypatch):
    from stockwatch import ui
    (tmp_path / "data").mkdir()
    save_config(tmp_path / "config.yaml", {"portfolio": {"base_currency": "USD"}, "watchlist": {}})
    save_transactions(tmp_path / "data/transactions.csv", [])
    (tmp_path / "examples").symlink_to(ROOT / "examples", target_is_directory=True)
    monkeypatch.setattr(ui, "ROOT", tmp_path)
    from stockwatch.providers.base import Quote
    monkeypatch.setattr(ui, "cached_instrument_quote", lambda symbol, demo: Quote(symbol, price=20, previous_close=19, year_high=25, year_low=10))
    # Keep UI navigation offline; detailed provider reuse is checked by provider tests.
    monkeypatch.setattr(ui, "cached_watchlist_snapshot", lambda config, rows, day, demo: (*ui.cached_snapshot(config, rows, day, demo), {}))
    ui.cached_snapshot.clear()
    ui.cached_history.clear()
    return AppTest.from_file(str(ROOT / "app.py"), default_timeout=15), tmp_path


@pytest.mark.parametrize("page", ["Dashboard", "Performance", "Holdings", "Transactions", "Watchlist & Alerts", "Settings"])
def test_all_pages_empty_and_demo(app, page):
    app_test, _ = app
    app_test.run()
    app_test.sidebar.radio[0].set_value(page).run()
    assert not app_test.exception
    app_test.sidebar.toggle[0].set_value(True).run()
    assert not app_test.exception and "SIMULATED" in app_test.warning[0].value
    if page == "Dashboard":
        assert len(app_test.metric) == 5 and len(app_test.dataframe) == 1


def test_transactions_editor_save(app):
    app_test, root = app
    app_test.run()
    save_transactions(root / "data/transactions.csv", [{"date": "2026-09-28", "symbol": "XYZ", "side": "BUY", "shares": "1", "price": "10", "note": "old"}])
    # Navigate directly after adding the row: no live quote requests in offline tests.
    app_test.sidebar.radio[0].set_value("Transactions").run()
    app_test.session_state["transactions_editor_False"] = {"edited_rows": {0: {"shares": "2.5", "note": "edited"}}, "added_rows": [], "deleted_rows": []}
    next(button for button in app_test.button if button.label == "Save transactions").click().run()
    assert not app_test.exception
    assert str(load_transactions(root / "data/transactions.csv")[0].shares) == "2.5"


def test_search_select_and_add_transaction(app, monkeypatch):
    from datetime import date
    from stockwatch.providers.base import Instrument
    from stockwatch import ui
    calls = []
    def search(query, demo):
        calls.append(query)
        return [Instrument("XYZ", "Example Inc", "NASDAQ", "EQUITY")]
    monkeypatch.setattr(ui, "cached_search", search)
    app_test, root = app
    app_test.run()
    app_test.sidebar.radio[0].set_value("Transactions").run()
    assert not calls  # Searching is explicit, not performed on every rerun.
    next(widget for widget in app_test.text_input if widget.label == "Stock name or ticker").set_value("Example")
    next(button for button in app_test.button if button.label == "Search stocks").click().run()
    selected = next(widget for widget in app_test.selectbox if widget.label == "Choose stock / ETF")
    assert "Example Inc" in selected.options[0]
    selected.set_value("XYZ").run()
    app_test.date_input[0].set_value(date(2026, 9, 28))
    next(widget for widget in app_test.number_input if widget.label == "Execution price (USD)").set_value(90.0)
    next(button for button in app_test.button if button.label == "Add transaction").click().run()
    assert not app_test.exception
    ledger = load_transactions(root / "data/transactions.csv")
    assert len(ledger) == 1 and ledger[0].symbol == "XYZ" and ledger[0].price == 90
    assert ledger[0].side == "BUY" and ledger[0].date == date(2026, 9, 28)
    assert calls == ["Example"]
    # An invalid sell preserves the already saved transaction.
    next(widget for widget in app_test.selectbox if widget.label == "Transaction direction").set_value("SELL")
    next(widget for widget in app_test.number_input if widget.label == "Shares").set_value(2.0)
    next(button for button in app_test.button if button.label == "Add transaction").click().run()
    assert app_test.error and len(load_transactions(root / "data/transactions.csv")) == 1


def test_search_failure_manual_fallback(app, monkeypatch):
    from stockwatch import ui
    from stockwatch.providers.base import DataUnavailable
    def failing_search(*_):
        raise DataUnavailable("Search unavailable")
    monkeypatch.setattr(ui, "cached_search", failing_search)
    app_test, root = app
    app_test.run()
    app_test.sidebar.radio[0].set_value("Transactions").run()
    next(widget for widget in app_test.text_input if widget.label == "Stock name or ticker").set_value("XYZ")
    next(button for button in app_test.button if button.label == "Search stocks").click().run()
    assert "temporarily unavailable" in app_test.warning[0].value
    app_test.checkbox[0].set_value(True).run()
    next(widget for widget in app_test.text_input if widget.label == "Symbol").set_value("xyz").run()
    next(widget for widget in app_test.number_input if widget.label == "Execution price (USD)").set_value(10.0)
    next(button for button in app_test.button if button.label == "Add transaction").click().run()
    assert not app_test.exception and load_transactions(root / "data/transactions.csv")[0].symbol == "XYZ"


def test_watchlist_quick_form_updates_and_disables_rules(app, monkeypatch):
    from stockwatch import ui
    from stockwatch.portfolio import calculate
    from stockwatch.providers.base import Quote
    class Snapshot:
        def __call__(self, config, rows, as_of, demo):
            return calculate({}, {}), {symbol: Quote(symbol, error="Data unavailable") for symbol in config["watchlist"]}
        def clear(self):
            pass
    monkeypatch.setattr(ui, "cached_snapshot", Snapshot())
    app_test, root = app
    app_test.run()
    app_test.sidebar.radio[0].set_value("Watchlist & Alerts").run()
    app_test.checkbox[0].set_value(True).run()
    next(widget for widget in app_test.text_input if widget.label == "Symbol").set_value("XYZ").run()
    next(widget for widget in app_test.selectbox if widget.label == "Status").set_value("waiting")
    next(widget for widget in app_test.number_input if widget.label == "Buy Below").set_value(18.0)
    next(widget for widget in app_test.number_input if widget.label == "Below").set_value(20.0)
    next(widget for widget in app_test.number_input if widget.label == "Daily Move %").set_value(5.0)
    next(button for button in app_test.button if button.label == "Save this stock").click().run()
    assert not app_test.exception
    assert load_config(root / "config.yaml")["watchlist"]["XYZ"]["alerts"] == {"below": 20, "daily_move_pct": 5}
    assert load_config(root / "config.yaml")["watchlist"]["XYZ"]["status"] == "waiting"
    assert load_config(root / "config.yaml")["watchlist"]["XYZ"]["buy_below"] == 18
    next(widget for widget in app_test.number_input if widget.label == "Below").set_value(0.0)
    next(widget for widget in app_test.number_input if widget.label == "Daily Move %").set_value(0.0)
    next(button for button in app_test.button if button.label == "Save this stock").click().run()
    assert "alerts" not in load_config(root / "config.yaml")["watchlist"]["XYZ"]


def test_watchlist_editor_save_without_live_data(app):
    app_test, root = app
    app_test.run()
    app_test.sidebar.radio[0].set_value("Watchlist & Alerts").run()
    app_test.session_state["watchlist_editor_False"] = {"edited_rows": {}, "deleted_rows": [], "added_rows": [
        {"Symbol": "XYZ", "Thesis": "Test facts", "Target Shares": 5.0, "Below": 90.0, "Daily Move %": 5.0}]}
    # Save triggers a rerun with a watched ticker; substitute the cached service.
    import stockwatch.ui as ui
    from stockwatch.providers.base import Quote
    from stockwatch.portfolio import calculate
    original = ui.cached_snapshot
    class Stub:
        def __call__(self, config, rows, as_of, demo):
            return calculate({}, {}), {symbol: Quote(symbol, error="Data unavailable") for symbol in config["watchlist"]}
        def clear(self):
            pass
    ui.cached_snapshot = Stub()
    try:
        next(button for button in app_test.button if button.label == "Save watchlist").click().run()
    finally:
        ui.cached_snapshot = original
    assert not app_test.exception
    assert load_config(root / "config.yaml")["watchlist"]["XYZ"]["alerts"]["below"] == 90


@pytest.mark.parametrize("page", ["Dashboard", "Performance", "Holdings", "Transactions", "Watchlist & Alerts", "Settings"])
def test_chinese_pages_empty_and_demo(app, page):
    app_test, root = app
    save_config(root / "config.yaml", {"portfolio": {"base_currency": "USD", "language": "zh-CN"}, "watchlist": {}})
    app_test.run()
    app_test.sidebar.radio[0].set_value(page).run()
    assert not app_test.exception
    assert app_test.title[0].value == t(page, "zh-CN")
    assert app_test.sidebar.toggle[0].label == "演示模式"
    app_test.sidebar.toggle[0].set_value(True).run()
    assert not app_test.exception and "模拟演示数据" in app_test.warning[0].value
    if page == "Dashboard":
        assert app_test.metric[0].label == "持仓成本"
        assert "股票代码" in app_test.dataframe[0].value.columns
    if page == "Watchlist & Alerts":
        candidates, owned = app_test.dataframe[0].value, app_test.dataframe[1].value
        assert list(candidates["股票代码"]) == ["CAND"]
        assert set(owned["股票代码"]) == {"CORE", "TECH", "VALUE"}
        assert "近5日涨跌" in candidates.columns and "距候选目标价" in candidates.columns
        assert candidates.iloc[0]["候选状态"] == "等待价格"


def test_language_switch_persists_and_transaction_data_unchanged(app):
    app_test, root = app
    original = (root / "data/transactions.csv").read_bytes()
    app_test.run()
    app_test.sidebar.radio[0].set_value("Settings").run()
    next(select for select in app_test.selectbox if select.label == "Language").set_value("zh-CN")
    next(button for button in app_test.button if button.label == "Save settings").click().run()
    assert not app_test.exception and app_test.title[0].value == "设置"
    assert "设置已保存" in app_test.success[0].value
    assert load_config(root / "config.yaml")["portfolio"]["language"] == "zh-CN"
    app_test = AppTest.from_file(str(ROOT / "app.py"), default_timeout=15).run()
    assert app_test.title[0].value == "总览"
    app_test.sidebar.radio[0].set_value("Settings").run()
    next(select for select in app_test.selectbox if select.label == "界面与日报语言").set_value("en")
    next(button for button in app_test.button if button.label == "保存设置").click().run()
    assert not app_test.exception and app_test.title[0].value == "Settings"
    assert (root / "data/transactions.csv").read_bytes() == original


def test_workflow_permissions_schedule_and_file_whitelist():
    workflow = yaml.load((ROOT / ".github/workflows/daily.yml").read_text(), Loader=yaml.BaseLoader)
    assert workflow["permissions"] == {"contents": "write"}
    assert workflow["on"]["schedule"] == [
        {"cron": "23 14 * * 1-5"}, {"cron": "23 15 * * 1-5"},
        {"cron": "53 22 * * 1-5"}, {"cron": "53 23 * * 1-5"}]
    assert set(workflow["on"]) == {"schedule", "workflow_dispatch"}
    assert workflow["concurrency"]["cancel-in-progress"] == "false"
    steps = workflow["jobs"]["daily"]["steps"]
    assert all("pytest" not in step.get("run", "") and "pip check" not in step.get("run", "") for step in steps)
    assert workflow["on"]["workflow_dispatch"]["inputs"]["mode"]["options"] == ["CLOSE", "INTRADAY"]
    command = next(step for step in steps if step.get("id") == "report")
    assert "steps.schedule.outputs.mode" in command["env"]["REPORT_MODE"] and '--mode "$REPORT_MODE"' in command["run"]
    selector = next(step for step in steps if step.get("id") == "schedule")
    assert "github.event.schedule" in selector["env"]["SCHEDULE_CRON"]
    assert steps.index(selector) < next(i for i, step in enumerate(steps) if step["name"] == "Set up Python")
    for name in ("Set up Python", "Install locked dependencies", "Generate and deliver daily report"):
        assert next(step for step in steps if step["name"] == name)["if"] == "steps.schedule.outputs.should_run == 'true'"
    assert "inputs.scheduled" in command["env"]["SCHEDULED"]
    assert workflow["on"]["workflow_dispatch"]["inputs"]["scheduled"]["default"] == "false"
    assert "vars.STOCKWATCH_SCHEDULER" in workflow["jobs"]["daily"]["if"]
    assert "github.event.repository.private == true" in workflow["jobs"]["daily"]["if"]
    persistence = next(step["run"] for step in steps if step["name"] == "Validate and persist notification state")
    assert "git add -f -- data/state.json" in persistence and "git add ." not in persistence
    assert "chore: update StockWatch state [skip ci]" in persistence
    artifact = next(step for step in steps if step["name"] == "Save logs and recovery state")
    assert artifact["with"]["retention-days"] == "3"
    ci = yaml.load((ROOT / ".github/workflows/ci.yml").read_text(), Loader=yaml.BaseLoader)
    assert set(ci["on"]) == {"push", "pull_request"} and ci["permissions"] == {"contents": "read"}
    commands = "\n".join(step.get("run", "") for step in ci["jobs"]["test"]["steps"])
    assert "python -m pytest" in commands and "python -m pip check" in commands
    assert "stockwatch.daily" not in commands


def test_ui_and_daily_do_not_import_openbb():
    import ast
    for relative in ("stockwatch/ui.py", "stockwatch/daily.py", "stockwatch/services.py"):
        tree = ast.parse((ROOT / relative).read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                assert all(not name.name.startswith("openbb") for name in node.names)
            if isinstance(node, ast.ImportFrom):
                assert not (node.module or "").startswith("openbb")


def test_chinese_control_center_toggle_and_preview(app):
    app_test, root = app
    save_config(root / "config.yaml", {"portfolio": {"language": "zh-CN"}, "watchlist": {}})
    app_test.run()
    original = (root / "data/transactions.csv").read_bytes()
    next(toggle for toggle in app_test.toggle if toggle.label == "邮件通知").set_value(False).run()
    assert not app_test.exception
    assert not load_config(root / "config.yaml")["notifications"]["email_enabled"]
    app_test.sidebar.radio[0].set_value("Settings").run()
    assert any(section.value == "自动化设置中心" for section in app_test.subheader)
    assert any(button.label == "手动运行任务" for button in app_test.button)
    assert (root / "data/transactions.csv").read_bytes() == original
    app_test.sidebar.toggle[0].set_value(True).run()
    assert not app_test.exception
    assert (root / "examples/config.yaml").read_bytes() == (ROOT / "examples/config.yaml").read_bytes()
    app_test.sidebar.radio[0].set_value("Performance").run()
    assert not app_test.exception and len(app_test.metric) == 7
    assert not (root / "data/performance.json").exists()
    next(select for select in app_test.selectbox if select.label == "时间范围").set_value("5D").run()
    assert not app_test.exception


def test_hsbc_settings_and_cloud_sync_only_entry(app, monkeypatch):
    app_test, root=app
    save_config(root / "config.yaml", {"portfolio":{"language":"zh-CN"},"watchlist":{}})
    app_test.run()
    app_test.sidebar.radio[0].set_value("Settings").run()
    next(box for box in app_test.checkbox if box.label=="自动导入汇丰已完成成交").set_value(True)
    next(button for button in app_test.button if button.label=="保存汇丰导入设置").click().run()
    assert not app_test.exception and load_config(root / "config.yaml")["imports"]["hsbc"]["enabled"]
    app_test.sidebar.radio[0].set_value("Dashboard").run()
    calls=[]
    monkeypatch.setattr("stockwatch.ui.configuration_status", lambda:{"GMAIL_ADDRESS":False,"GMAIL_APP_PASSWORD":False,"REPORT_EMAIL":False})
    monkeypatch.setattr("stockwatch.ui.trigger_workflow", lambda *a,**kw:calls.append(kw))
    next(button for button in app_test.button if button.label=="立即同步 HSBC 成交记录").click().run()
    assert not app_test.exception and calls==[{"dry_run":False,"sync_only":True,"lookback_days":3}]
    assert "不发送日报" in app_test.success[0].value


def test_local_gmail_form_save_masks_password_and_does_not_change_cloud(app):
    app_test,root=app
    app_test.run()
    assert not any(button.label=="Start service" for button in app_test.button)
    next(widget for widget in app_test.text_input if widget.label=="Local Gmail Address").set_value("sender@example.com")
    next(widget for widget in app_test.text_input if widget.label=="Local Report Email").set_value("recipient@example.com")
    next(widget for widget in app_test.text_input if widget.label=="Local Gmail App Password").set_value("synthetic-password")
    original=(root/"config.yaml").read_bytes()
    next(button for button in app_test.button if button.label=="Save local Gmail").click().run()
    assert not app_test.exception
    from stockwatch.notifications.local import load_local
    assert load_local(root)["GMAIL_ADDRESS"]=="sender@example.com"
    assert (root/"config.yaml").read_bytes()==original
    assert next(widget for widget in app_test.text_input if widget.label=="Local Gmail App Password").value==""
    assert all("synthetic-password" not in widget.value for widget in app_test.success)
    next(button for button in app_test.button if button.label=="Remove saved local Gmail").click().run()
    assert not app_test.exception and not (root/".stockwatch/gmail.json").exists()
    app_test.sidebar.radio[0].set_value("Settings").run()
    assert not any(button.label=="Start service" for button in app_test.button)


def test_search_changes_selection_shows_quote_and_resets_execution_price(app, monkeypatch):
    from stockwatch import ui
    from stockwatch.providers.base import Instrument
    monkeypatch.setattr(ui,"cached_search",lambda query,demo:[Instrument("NEW" if query=="second" else "XYZ","Example","NASDAQ","EQUITY")])
    app_test,_=app
    app_test.run()
    app_test.sidebar.radio[0].set_value("Transactions").run()
    next(w for w in app_test.text_input if w.label=="Stock name or ticker").set_value("first").run()
    assert next(w for w in app_test.selectbox if w.label=="Choose stock / ETF").value=="XYZ"
    assert any(w.label=="Current Price" and w.value=="$20.00" for w in app_test.metric)
    next(w for w in app_test.number_input if w.label=="Execution price (USD)").set_value(18)
    next(w for w in app_test.text_input if w.label=="Stock name or ticker").set_value("second").run()
    assert next(w for w in app_test.selectbox if w.label=="Choose stock / ETF").value=="NEW"
    assert next(w for w in app_test.number_input if w.label=="Execution price (USD)").value==0


def test_holding_notes_edit_and_charts_exclude_watch_only_symbols(app, monkeypatch):
    from stockwatch import ui
    from stockwatch.portfolio import calculate,positions
    from stockwatch.providers.base import Quote
    from datetime import date
    app_test,root=app
    save_transactions(root/"data/transactions.csv",[{"date":"2026-09-28","symbol":"XYZ","side":"BUY","shares":"1","price":"10"}])
    save_config(root/"config.yaml",{"portfolio":{},"watchlist":{"WATCH":{"thesis":"observation"}}})
    def snapshot(config,rows,day,demo):
        assert config["watchlist"]=={}
        from stockwatch.storage import validate_transactions
        quotes={"XYZ":Quote("XYZ",price=12,previous_close=11)}
        return calculate(positions(validate_transactions(rows),date(2026,10,1)),quotes),quotes
    snapshot.clear=lambda:None
    monkeypatch.setattr(ui,"cached_snapshot",snapshot)
    selected=[]
    monkeypatch.setattr(ui,"chart_prices",lambda symbols,*a:selected.append(symbols))
    app_test.run()
    app_test.sidebar.radio[0].set_value("Holdings").run()
    next(w for w in app_test.text_area if w.label=="Thesis").set_value("Personal note")
    next(w for w in app_test.button if w.label=="Save holding notes").click().run()
    assert not app_test.exception
    assert load_config(root/"config.yaml")["watchlist"]["XYZ"]["thesis"]=="Personal note"
    assert selected[-1]==["XYZ"]
    assert load_config(root/"config.yaml")["watchlist"]["WATCH"]["thesis"]=="observation"


def test_optional_dashboard_watch_chart_is_lazy_and_independent(app, monkeypatch):
    from stockwatch import ui
    import pandas as pd
    from datetime import date
    app_test,root=app
    save_config(root/"config.yaml",{"portfolio":{},"watchlist":{"WATCH":{}}})
    requested=[]
    def history(symbol,period,day,demo):
        requested.append((symbol,period))
        return pd.DataFrame({"date":[date(2026,9,28),date(2026,9,29)],"close":[10,11]})
    monkeypatch.setattr(ui,"cached_history",history)
    app_test.run()
    watch=next(w for w in app_test.selectbox if w.label=="Watchlist stock")
    assert watch.value is None and requested==[]
    watch.set_value("WATCH").run()
    assert not app_test.exception and requested==[("WATCH","1M")]
    next(w for w in app_test.selectbox if w.key=="dashboard_watchlist_history_period").set_value("5D").run()
    assert requested[-1]==("WATCH","5D")


def test_settings_has_no_stock_search_and_cloud_controls_are_grouped(app):
    app_test,_=app
    app_test.run()
    app_test.sidebar.radio[0].set_value("Settings").run()
    assert not app_test.exception
    assert not any(w.label=="Search stocks" for w in app_test.button)
    assert not any(w.label=="Stock name or ticker" for w in app_test.text_input)
    cloud=next(w for w in app_test.expander if w.label=="Cloud automation (GitHub Actions)")
    assert not cloud.proto.expanded
    assert any(w.label=="Enable cloud daily workflow" for w in cloud.button)
    assert next(w for w in app_test.text_input if w.label=="Benchmark ticker").value=="SPY"


def test_dashboard_and_watchlist_offer_ai_download(app):
    app_test, _ = app
    app_test.run()
    assert not app_test.exception
    assert app_test.get("download_button")[0].proto.label == "Export current AI data (JSON)"
    app_test.sidebar.radio[0].set_value("Watchlist & Alerts").run()
    assert not app_test.exception
    assert app_test.get("download_button")[0].proto.label == "Export current AI data (JSON)"
