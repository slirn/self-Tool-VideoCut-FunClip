# REQ-20260915-007 — 修复：字幕修订「大模型分析」报 NoneType 异常（真实错误被掩盖）

| 字段 | 值 |
|---|---|
| 编号 | REQ-20260915-007 |
| 日期 | 2026-09-15 |
| 优先级 | P1（核心功能不可用 + 错误信息误导，用户报告） |
| 状态 | ✅ 已完成（验证记录见 §4） |
| 关联 | REQ-20260915-005（字幕修订服务原始实现） |
| 改动范围 | 仅 `slirn_home/revision_service.py`；新增测试 `tests/test_revision.py` |

## 1. 缺陷与根因

### 现象（用户报告）
任务 20260915-002（真实长视频，**389 段**字幕）点击「🤖 大模型分析字幕」→
红色提示「❌ 大模型调用失败： **'NoneType' object is not subscriptable**」。

### 根因（实测复现）
用任务 002 第 1 批（40 段）真实调用 DashScope 复现，原始返回：

```
status_code: 400, code: "Arrearage", output: None
message: Access denied, please make sure your account is in good standing...
```

两层问题：

1. **触发环境**：阿里云百炼**账户欠费**，所有 LLM 调用被服务端拒绝（此前 E2E
   能通过是因为当时账户可用）。
2. **代码缺陷（本 REQ 修复对象）**：DashScope 对业务失败**不抛异常**，而是返回
   `status_code != 200`、`output = None` 的响应对象；`_call_llm` 不检查
   `status_code` 直接 `resp["output"]["choices"][0]["message"]["content"]` →
   `'NoneType' object is not subscriptable`。欠费/限流/Key 无效等真实原因
   （code/message/request_id）全部被掩盖，用户看到的是无法定位的 Python 报错。

### 暴露出的次级健壮性问题（同批修复）
- 389 段按 40 段/批串行调用，批间无间隔、无输出截断（`finish_reason=length`）处理；
  单批解析失败即废掉整个任务的分析结果。

## 2. 修复方案

| ID | 修复 |
|---|---|
| REQ-7.1 | `_call_llm` 响应硬校验：`status_code != 200` → 抛出含 `code`/中文说明/`message`/`request_id` 的 RuntimeError（常见错误码中文映射：Arrearage→账户欠费、InvalidApiKey→Key 无效、Throttling→限流）；正文/choices 防御式提取，缺失即明确报错 |
| REQ-7.2 | `finish_reason == "length"`（输出被截断）→ ValueError，由批处理减半重试消化 |
| REQ-7.3 | 批处理健壮化：`BATCH_SIZE` 40→20（降低截断概率）；批间 `sleep 0.6s` 防限流；**解析失败/截断 → 批减半递归重试**，单段仍失败 → 回填 `review`（note 说明原因）不废整个任务；**确定性失败**（欠费等 RuntimeError）→ 立即终止 job 并给出可操作信息 |
| REQ-7.4 | 重试升级：次数 1→2，退避 2s/4s |

设计要点：欠费这类确定性失败**不做**减半重试（每批都会失败，减半只浪费调用），
直接终止并把「请充值」信息透传到 UI toast；只有输出质量问题（JSON 解析失败/截断）
走减半，瞬时网络/限流错误走重试。

## 3. 验收标准

- [x] AC-1 mock 欠费响应（400/Arrearage/output=None）→ `_call_llm` 抛出的错误信息
  含 `Arrearage`、中文「欠费」说明、`request_id`，不再是 NoneType
- [x] AC-2 mock 限流响应 → 退避重试后成功；重试耗尽 → 明确报错
- [x] AC-3 mock 输出截断（finish_reason=length）→ ValueError 触发批减半；
  mock 大批解析失败 → 减半后成功，job done 且段数完整
- [x] AC-4 mock 全部解析失败 → 单段回填 review（note 含「请人工复核」），job done 不崩
- [x] AC-5 服务重启后对任务 002 实测点击「大模型分析字幕」→ toast 显示
  「阿里云百炼 [Arrearage] 账户欠费…」可操作信息（而非 NoneType）
- [x] AC-6 pytest 全量通过 + ruff 0 错

## 4. 验证记录（2026-09-15）

- 复现脚本：`work/REQ-20260915-007-revise-llm-crash/_repro.py` — 真实调用捕获
  `400 Arrearage / output=None`（根因证据）
- **AC-1/2/3 单测**：`tests/test_revision.py` 新增 5 个用例
  （业务错误可读性、限流重试、截断 ValueError、减半恢复、单段回填）全过
- **AC-4**：`test_start_job_backfills_on_total_parse_failure` — job done、
  3/3 回填 review
- **AC-5**：重启服务 → `POST /slirn/api/revise_subtitle`（任务 002）→
  `revise_status` 返回 error 文本为「大模型调用失败: 阿里云百炼 [Arrearage]
  账户欠费/余额不足，请到阿里云百炼控制台充值后重试：Access denied…（request_id=…）」；
  CDP 浏览器截图见 work 目录
- **AC-6**：`pytest tests/ -q` 99 passed；`ruff check .` 0 错
- 说明：账户充值前真实分析无法出结果（环境问题，非代码问题）；修复后错误信息
  已能直接指导用户充值

## 5. 非目标

- ❌ 不改 UI/JS（错误信息经既有 job error → toast 链路透传）
- ❌ 不处理充值/配额（用户侧环境问题）
- ❌ 不改 funclip/ 与 tasklib
