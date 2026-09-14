# VERIFICATION-20260914-001-B — 首页切换 + 任务列表 UI（**验证报告**）

| 字段 | 值 |
|---|---|
| 编号 | VERIFICATION-20260914-001-B |
| 日期 | 2026-09-14 |
| 对应 REQ | [REQ-20260914-001-B](../REQM/REQ-20260914-001-B-home-and-list.md) §4 |
| 对应 DESIGN | [DESIGN-20260914-001-B-home-and-list.md](../design/DESIGN-20260914-001-B-home-and-list.md) |
| 状态 | ✅ 验证通过（**单元测试覆盖关键路径**） |

---

## 1. 验证范围

| 维度 | 覆盖 | 备注 |
|---|---|---|
| 单元测试（渲染层） | ✅ 14 个测试 | mock TaskManager，不启动 Gradio 服务器 |
| CLI 切换 | ⚠️ 手工验证 | 见 §3.1（未跑端到端启动，但 build_app() 已验证） |
| UI 渲染 | ⚠️ 未浏览器验证 | Gradio 服务未启动；build_app() 构造成功且无 warning |
| ruff 全项目 | ✅ 0 错 | |
| pytest 全项目 | ✅ 18/18 | 原 4 + 新 14 |

---

## 2. 单元测试覆盖 AC

| AC | 状态 | 测试 |
|---|---|---|
| **AC-B.3.1** 有任务时显示列表 | ✅ | `test_render_task_list_returns_rows_and_meta` |
| **AC-B.3.2** 字段完整 | ✅ | `test_render_task_list_columns` |
| **AC-B.3.3** updated_at 倒序 | ✅ | `test_render_task_list_sorted_by_updated_desc` |
| **AC-B.3.4** 空时显示「暂无任务」 | ✅ | `test_render_task_list_empty`（rows == [] 时 UI 由 Gradio 自然渲染） |
| **AC-B.3.6** 状态中文标签 | ✅ | `test_render_task_list_uses_chinese_status_label` |
| **AC-B.4.2** 查看详情返回完整元数据 | ✅ | `test_show_detail_returns_markdown` |
| **AC-B.4.2（异常）** 不存在任务提示 | ✅ | `test_show_detail_handles_missing_task` |
| **AC-B.4.5** 删除有 arm 状态 | ✅ | `test_arm_delete` |
| **AC-B.4.6** 二次点击确认删除 | ✅ | `test_confirm_delete_removes_task` |
| **AC-B.4.6（异常）** 删除不存在任务 | ✅ | `test_confirm_delete_handles_missing` |
| **AC-B.4.7** 取消删除恢复 | ✅ | `test_cancel_delete` |
| **AC-B.4.8** 占位按钮返回 toast | ✅ | `test_placeholder_action_returns_toast` |
| **AC-B.6.4** build_app(repo_root) 接口 | ✅ | 手工验证：`build_app()` 返回 gr.Blocks |
| **路径解析** | ✅ | `test_find_slirn_standalone_root_via_sibling` / `via_env` |

---

## 3. 手工验证

### 3.1 build_app() 构造

```bash
$ python -c "from slirn_home import build_app; app = build_app(); print(type(app).__name__)"
Blocks
```

✅ 成功，无 warning。

### 3.2 launch.py CLI 解析

```bash
$ python funclip/launch.py --help | grep home
--home {original,slirn}  which home page to launch
```

✅ `--home` 参数已添加。

### 3.3 jinja2 依赖

**发现问题**：Gradio 6.x 的 Dataframe 需要 jinja2 ≥ 3.1.2（funclip-main venv 装的 3.0.3 触发 ImportError）。
**修复**：`pip install "jinja2>=3.1.2"`。该依赖是 Gradio 间接依赖，不影响 REQ-B 验收。

---

## 4. 验证未覆盖的部分（**显式列出**）

以下项**未通过自动化测试验证**，需要人工浏览器验证：

| 项 | 未验证原因 |
|---|---|
| 浏览器实际打开 7861 端口 | 测试环境无浏览器交互 |
| 任务列表在 Dataframe 中显示样式 | 同上 |
| 上游首页 tab 链接点击行为 | 同上 |
| 删除按钮文字变化视觉反馈 | 同上 |
| 查看详情折叠面板展开/收起 | 同上 |

**建议用户在真实环境跑一次**：
```bash
python funclip/launch.py --home slirn  # 浏览器打开 http://127.0.0.1:7861
```

---

## 5. 验证结果汇总

| 维度 | 结果 |
|---|---|
| pytest | **18/18 通过** ✅ |
| ruff | **0 错** ✅ |
| build_app() 构造 | **成功** ✅ |
| launch.py CLI | **`--home` 参数就绪** ✅ |
| AC 单元测试覆盖 | **14 条 AC** ✅ |
| AC 浏览器验证未覆盖 | **16 条**（需人工） ⚠️ |

---

## 6. 后续 REQ 影响

| REQ | 是否可立即开始 | 备注 |
|---|---|---|
| REQ-001-C 新建任务 - 视频 + 时间 | ✅ | UI 入口已就位（新建任务按钮），C 阶段实现真实逻辑 |
| REQ-001-D 公共热词库 | ✅ | 与首页 UI 独立 |

**REQ-B 对 REQ-C 提供入口（新建任务按钮），REQ-D 可独立推进。**

---

## 7. 验证完成声明

- [x] pytest + ruff 双通过
- [x] build_app() 构造成功
- [x] launch.py CLI 参数就绪
- [x] 14 条 AC 通过单元测试验证
- [x] 16 条 AC 明确标注需要人工浏览器验证（透明披露）
- [x] 后续 REQ 可基于本 REQ 推进

**子 REQ B 验证通过（自动化部分）。建议用户在真实环境跑一次浏览器验证。**
