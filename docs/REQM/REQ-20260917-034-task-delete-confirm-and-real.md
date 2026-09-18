# REQ-20260917-034 — 任务删除：确认防误删 + 真删落盘 + 如实报错

| 字段 | 值 |
|---|---|
| 编号 | REQ-20260917-034 |
| 日期 | 2026-09-18 |
| 优先级 | P1（数据安全：误删风险 + 删除假成功） |
| 状态 | ✅ 已完成（单元 + 真实浏览器 E2E 全过） |
| 关联 | tasklib TaskManager.delete（REQ-A） |
| 改动范围 | `slirn_home/`（app.py 端点 + 两处按钮 + router.js）；`tasklib/manager.py` delete 加固（slirn-standalone 仓库）；funclip/ 零改动 |

---

## 0. 背景（两个用户报告的缺陷）

1. **无确认**：任务列表 / 任务详情的「🗑️ 删除」点击即删，无任何确认 — 容易误删。
2. **假删除**：用户实测「页面上删除了，刷新又回来了」。

根因链（已复现）：`TaskManager.delete()` 本身是 `shutil.rmtree`（真删），
但端点 [app.py delete_task] 把异常整个吞掉（`except Exception: pass`）
照样返回「✅ 已删除」。Windows 下任务文件被占用时（典型：任务视频还在
预览，流式播放端点握着句柄 — 已用最小用例复现 `WinError 32`）
rmtree 抛 OSError → 被吞 → 假成功 → 刷新后任务从磁盘重新列出来。

## 1. 核心需求

| ID | 需求 |
|---|---|
| REQ-1.1 | 点「删除」先弹确认框，明确告知**不可恢复**且会删除任务全部文件，带任务名 |
| REQ-1.2 | 确认后执行**真删**：任务目录（原视频链接、字幕、切分决策、合成产物等）从磁盘移除 |
| REQ-1.3 | 删除前释放页面上正在预览的任务视频（暂停 + 摘除 src），减少 Windows 句柄占用 |
| REQ-1.4 | 删除失败必须**如实报错**（含系统原因 + 「关闭预览后重试」指引），不再假成功 |
| REQ-1.5 | manager.delete 加固：只读属性自动清位；占用重试 3 次（占用常为瞬态）；metadata.json 最后删（中途失败任务仍完整可见，不留幽灵目录） |

## 2. 设计要点

- 确认框用项目既有惯用法 `window.confirm`（router.js 已有 9 处），文案带任务名
  （按钮新增 `data-task-name`，JS 不做 DOM 猜测）。
- 端点不再吞异常：缺 tid / 任务不存在 → `_err`；OSError → log.exception + `_err`
  （`handleResp` 对 `ok:false` 自动 toast，前端零额外代码）。
- `delete()` 顺序：先删 metadata.json 以外的子项 → 最后删 metadata.json → rmdir；
  失败清只读位重试（0.6s × 3），仍失败抛最后一次 OSError。

## 3. 验收标准

- [x] AC-1 点删除 → 确认框出现（含任务名与「永久删除、不可恢复」）；取消 → 不发请求、任务还在
- [x] AC-2 确认 → 任务目录从磁盘消失（真删），列表刷新不再出现
- [x] AC-3 文件被占用时（复现 WinError 32）→ toast 报「删除失败」+ 占用指引，**不再假成功**；句柄释放后重删成功
- [x] AC-4 只读文件的任务可正常删除（自动清只读位）
- [x] AC-5 缺 task_id / 任务不存在 → 明确报错而非「已删除」
- [x] AC-6 单元测试覆盖端点 4 分支 + manager 加固；ruff 0 错；全量 pytest 通过
- [x] AC-7 真实浏览器 E2E：取消路径 + 确认路径（磁盘验证）+ 占用失败路径

## 4. 验证记录

**2026-09-18（实现 + 单元 + 真实浏览器 E2E）**

- 实现：`tasklib/manager.py` delete 重写（metadata.json 最后删 — 中途失败任务仍完整可见，
  不留幽灵目录；只读清位 + 3 次重试；仍失败如实抛 OSError）；app.py 两处删除按钮加
  `data-task-name`，端点吞异常改为分支报错（缺 tid / TaskNotFoundError /
  OSError→log.exception + 「关闭预览后重试」指引），成功 toast「✅ 已删除（任务文件
  已从磁盘移除）」；router.js delete-task 分支加 `window.confirm`（任务名 + 永久删除
  不可恢复警示）+ 删前释放所有 `<video>`（pause + removeAttribute('src') + load —
  Windows 句柄占用是「假删除」的直接诱因）。
- 质量：`ruff check slirn_home/ tests/` 0 错；pytest **256 passed**（新增
  tests/test_task_delete.py 7 用例：manager 真删/不存在抛错/只读删除/占用时
  metadata 幸存 + 列表仍可见 + 释放后成功；端点 成功含 data-task-name 与磁盘 toast/
  缺 tid/不存在/占用报错后释放重删成功）。
- E2E（真实浏览器 CDP 点击，任务 20260918-005/006/007）：
  - 取消：confirm 录制文案含「E2E-034-删除确认A」+「不可恢复」，目录仍在、卡片仍在；
  - 占用：toast「❌ 删除失败: [WinError 32] … — 可能有文件正被预览/占用，关闭预览后重试」，
    磁盘任务仍在（如实）；释放句柄重删 → 成功；
  - 确认：目录从磁盘消失、列表无该任务、toast 带「磁盘」。
  - 脚本：`work/REQ-20260917-034-task-delete/_e2e_del034_035_036.py`（gitignored），
    截图 `_shot_del034_*.png`。
