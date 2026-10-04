# 云端只读 Dashboard

可以从自己的私有 GitHub 仓库部署到 Streamlit Community Cloud，电脑关机后也能查看。部署后务必把 Viewer access 设为指定邮箱；只读模式限制应用操作，不替代访问认证。

## 部署

1. 在 Streamlit Community Cloud 选择自己的**私有仓库**、`main` 分支、`app.py`。
2. Advanced settings 选择 Python **3.11**。在 Secrets 编辑器中加入顶层设置：

   ```toml
   STOCKWATCH_READONLY = "1"
   ```

   Streamlit 会将顶层 Secrets 提供为环境变量。这里不需要 Gmail 密码、GitHub Token 或本机凭据。
3. 部署完成后打开 Sharing / Viewer access，仅允许自己指定的邮箱访问。不要把包含真实交易的应用设为公开访问。
4. `requirements.txt` 使用现有锁定依赖；首次安装可能较慢。

## 行情来源与刷新

- **在线优先**：云端通过 provider 层使用现有 yfinance 请求 Yahoo。盘中读取正常交易时段的报价，闭市读取最近完成的 NYSE session；保留五分钟缓存和手动刷新。
- 云端报价工作进程只加载 yfinance，避免在 Streamlit 之外加载完整 OpenBB。`auto_adjust=False`、关闭盘前盘后，沿用不计股息的价格口径；本机和日报仍使用 OpenBB / yfinance。
- **Actions 快照兜底**：CLOSE / INTRADAY 任务把已经取得的价格及最多约一年的精简日线保存到 `data/market_snapshot.json`。保存与邮件发送独立，缺少 Gmail 或 SMTP 失败不影响有效快照保存。日线尽量复用同一 provider 的请求缓存。
- 数据不完整时不伪造价格；全部请求失败时保留上一份文件。快照没有交易备注、投资逻辑、邮箱、凭据或提醒状态，但可能暴露用户关注的 ticker，因此也只保存在私有仓库。
- 在线失败时，同日期的快照可补缺。完整组合无法估值时可退回一组旧快照，**明确显示快照日期、生成时间及非实时提示**，按该日期计算持仓，晚于该日期的交易不计入旧视图。不会把旧收盘混入今天盈亏；盘中快照不能冒充最终收盘。
- 历史图表缺少区间端点时显示不可用，不缩短窗口，不把缺口补为零。云端历史预览仅使用可用报价与日线；长期表现以 Actions 保存的 `performance.json` 为准。
- 刷新按钮重新尝试在线行情；它不能强制刷新尚未运行的 Actions 快照。私有仓库更新后，Community Cloud 会更新运行的应用，实际时间由托管服务决定。

首次部署、暂时没有 Actions 快照时，可以在可信的本机运行（不发邮件、不导入交易、不改变提醒）：

```sh
python -m stockwatch.market_snapshot
# 确认 origin 对应自己的私有仓库后，仅提交该文件：
git add -f -- data/market_snapshot.json
git commit -m "chore: update dashboard market snapshot [skip ci]"
git push origin main
```

`--config`、`--transactions`、`--output` 可指定独立路径。公开示例仓库不要强制添加此文件。`daily --dry-run` / Demo 不更新正式快照。

## 只读与排查

- 可浏览六个页面、切换 Demo、导出 AI JSON、刷新行情和计算有限历史预览。
- 不允许保存用户数据、本机 Gmail、控制工作流、Git 同步或登录自启；邮件和提醒仍由原 Actions 任务管理。
- 日志只输出安全的错误分类：限流、网络、超时、依赖、进程被终止或空数据。`worker killed` 可能与资源限制有关，不等于已经确认内存不足。只有明确的 rate-limit / 429 分类才支持限流判断。
- 不保证免费的 Yahoo 在共享云 IP 上始终可用；快照可用性不代表在线报价恢复。Stooq 在当前验证中未返回可用报价，尚未作为实时源接入。
