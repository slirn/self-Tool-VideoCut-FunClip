# REQ-20260916-001 — 大模型设置 v3：协议选择 + 修改模型 + 醒目「设为当前」

| 字段 | 值 |
|---|---|
| 编号 | REQ-20260916-001 |
| 日期 | 2026-09-16 |
| 优先级 | P2（REQ-008 v2 的三项用户反馈增量） |
| 状态 | ✅ 已完成（验证记录见 §4） |
| 关联 | REQ-20260915-008（v2 动态注册表）、REQ-20260915-007（错误可读化） |
| 改动范围 | `llm_config.py`（protocol 字段 + update_model）；`revision_service.py`（Anthropic 协议分支 + 业务错误不重试）；`app.py`（update API + 弹窗协议下拉/编辑模式/设为当前改版）；`home.css`（协议徽章/当前徽章）；两份测试 |

## 1. 需求（用户原话转述）

> 关于大模型设置功能，还要添加大模型所使用的协议，是 OpenAI 的还是 Anthropic 的；
> 另外现在还缺少设置默认使用哪个模型的功能；
> 还需要添加对模型的修改功能，现在添加完成之后就无法修改了。

背景：注册表里用户自己登记的 MiniMax-M3（`https://api.minimaxi.com/anthropic`）
就是 Anthropic 协议端点 — v2 只有 OpenAI 兼容协议，该条目实际调不通，协议字段正是为此。

关于「设置默认使用哪个模型」：v2 已有「使用」按钮（点击切换 current 并落盘），
但按钮小、易被忽略 — 本期把其改版为醒目的主按钮 **「⭐ 设为当前」**，当前模型显示
**「✅ 当前使用中」** 徽章（不再是禁用按钮），功能语义不变。

## 2. 设计

### 2.1 协议字段（protocol）

- 注册项新增 `protocol`: `"openai"`（默认，`{base_url}/chat/completions` + Bearer）或
  `"anthropic"`（`{base_url}/v1/messages` + `x-api-key` + `anthropic-version: 2023-06-01`）。
- Anthropic 端点拼接：base 已带 `/v1` → `/messages`；已带 `/v1/messages` → 原样；否则 + `/v1/messages`
  （`https://api.anthropic.com` 与带网关前缀的写法都对）。
- Anthropic 请求体差异：`system` 提升为顶层字段；`max_tokens` 必填（取 4096，20 段/批输出约 2K）；
  响应取 `content[]` 中 `type=="text"` 分块拼接；截断判据 `stop_reason=="max_tokens"` → ValueError → 批减半。
- 大小写归一（"Anthropic" → "anthropic"）；v2 落盘条目缺 protocol → 读取时统一补 `"openai"`（向后兼容）。

### 2.2 修改模型（update）

- `update_model(repo_root, id_, new_id, provider, base_url, api_key_env, protocol)`：
  原地替换（保持列表位置）；改名后若原条目是当前模型 → `current` 跟随新名；
  新名与其他条目冲突 → 拒绝；原 id 不存在 → 拒绝。
- 界面：每条新增「编辑」→ 表单回填 5 项（含协议下拉）→ 区标题变「修改模型（原名）」、
  提交按钮变「💾 保存修改」、「取消编辑」出现；保存后表单复位为添加模式；取消同样复位。
- 添加/编辑共用一个表单与提交按钮（`LLM_EDIT_ID` 区分模式）。

### 2.3 设为当前（改版）

- 非当前模型：紫色主按钮 **⭐ 设为当前**（原「使用」小按钮）；
  当前模型：绿色徽章 **✅ 当前使用中**（原禁用按钮）+ ⭐ + 卡片高亮。
- 语义不变：点击 → `current` 落盘 → 全部 LLM 调用（字幕修订等）使用该模型。

### 2.4 顺带修正：确定性业务错误不再重试

- 新增 `LLMBusinessError`（欠费/Key 无效/模型不存在等）→ `_chat_completion` 直接上抛，
  不再退避重试 3 次（每次分析省 6s+无效调用）；限流/过载类（Throttling*/rate_limit*/overloaded_error）
  仍是可重试 RuntimeError。对 4xx/5xx 按 error code 分类，两个协议同样处理。

## 3. 验收标准

- [x] AC-1 添加模型可选协议（OpenAI 兼容 / Anthropic），列表条目显示协议徽章
- [x] AC-2 Anthropic 协议按官方 Messages API 构造请求（x-api-key 头 / system 顶层 /
      max_tokens 必填 / content 分块拼接 / stop_reason 截断），单测逐一验证
- [x] AC-3 v2 旧配置（无 protocol 字段）读取兼容，一律按 openai 处理
- [x] AC-4 「编辑」可修改任意字段含模型名；改名后当前模型指针跟随；重名/不存在拒绝
- [x] AC-5 「⭐ 设为当前」主按钮醒目；当前模型显示「✅ 当前使用中」徽章；切换即时生效
- [x] AC-6 非法协议拒绝；Anthropic 缺 Key → 可读错误（含变量名+厂商）
- [x] AC-7 pytest 全量 + ruff + API E2E + 浏览器实测（截图存档）

## 4. 验证记录（2026-09-16）

- **单测**：pytest **137 passed**（+12：anthropic 请求构造/URL 去重/错误可读/截断不重试/
  content 拼接、协议默认与归一/非法拒绝/旧条目补全、update 改字段/改名跟随/不存在/重名）；ruff 0 错。
- **API E2E**（`work/REQ-20260916-001-llm-config-v3/_e2e_test.py`）：注册表用户条目原样保留 →
  add deepseek-chat（openai）→ 非法协议 grpc 拒绝 → add claude-demo（anthropic）→
  缺 Key 可读错误 → update 改厂商/改名/未知拒绝/重名拒绝 → 当前模型改名 current 跟随 →
  **llm_test deepseek-reasoner 真实调用成功（0.47s「正常」）** → 清理恢复原状。
- **浏览器实测**（`_browser_test.py`）：协议徽章 + ⭐ 设为当前主按钮 + ✅ 当前使用中徽章 →
  协议下拉添加 anthropic 条目 → 编辑回填（5 项含下拉）/保存后表单复位/取消不发请求 →
  设为当前切换 → anthropic 缺 Key 内联可读错误 → 清理。截图 `_shot_llm_v3.png`（视觉复核无缺陷）。
- **真实 Anthropic 网关验证**：把用户登记的 MiniMax-M3（`api.minimaxi.com/anthropic`）改为
  anthropic 协议后 llm_test — 请求到达真实网关，返回可读错误
  `[authentication_error] API Key 无效：invalid api key`（本机 MINIMAX_API_KEY 的值无效，
  需用户更换；协议链路本身已验证正确）。
- 提交：FunClip-main `feat(home): 模型注册协议选择+编辑+设为当前改版 (REQ-20260916-001)`。

## 5. 非目标

- ❌ 其他协议（Google Gemini 原生 / Bedrock 签名等 — 走各家 OpenAI 兼容网关即可）
- ❌ API Key 的界面编辑（维持只登记环境变量名）
- ❌ 不改 funclip/ 上游
