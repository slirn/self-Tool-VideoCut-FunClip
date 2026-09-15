# REQ-20260915-008 — 大模型设置：动态注册多厂商模型 + 当前模型切换（v2）

| 字段 | 值 |
|---|---|
| 编号 | REQ-20260915-008 |
| 日期 | 2026-09-15（v2 重定义同日，验证记录跨 09-15/16） |
| 优先级 | P2（配置型功能；模型信息原来只能靠环境变量，用户无法在界面注册/切换） |
| 状态 | ✅ 已完成（验证记录见 §4） |
| 关联 | REQ-20260915-005（修订服务）、REQ-20260915-007（错误可读化） |
| 改动范围 | 新增 `slirn_home/llm_config.py`（模型注册表）；`revision_service.py`（OpenAI 兼容调用层 + entry 参数化）；`app.py`（⚙️ 注册弹窗 + 5 个 API）；`home.css`（弹窗样式）；`slirn-standalone/.gitignore`（+`config/`，子模块 bump） |

## 1. 需求

v1 原话：

> 添加一个模型配置功能，由用户设置当前系统使用的模型，在使用大模型时用用户指定的大模型，API key 从系统环境变量中获取并使用。

用户随后**重定义**（本文档按 v2 实现）：

> 大模型配置功能不是从已有列表去选择，而是用户可以根据自己所拥有的大模型去添加模型，添加时可以添加厂商信息、baseUrl、API KEY 对应的环境变量名等信息，这样可以达到动态添加模型，并且可以设置当前使用哪个模型。

核心诉求：
- **动态注册**：用户按自己拥有的模型自由添加（不从预设列表选）；
- 每个模型携带 4 项信息：**模型名、厂商、Base URL、API Key 对应的环境变量名**；
- 可随时**切换当前使用哪个模型**；
- **API Key 永不进界面/配置文件** — 只登记环境变量名，调用时从系统环境变量读取（v1 要求，v2 保留）。

## 2. 设计

### 2.1 模型注册表（存储与生效）

- 存储：`<repo_root>/config/llm.json`（机器本地运行时状态，与 `tasks/`、`hotwords/` 同级；
  slirn-standalone `.gitignore` 增加 `config/`，commit `d9bb75b`，子模块 bump）：

  ```json
  {
    "models": [
      {"id": "qwen-plus", "provider": "阿里云百炼",
       "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
       "api_key_env": "DASHSCOPE_API_KEY"}
    ],
    "current": "qwen-plus",
    "updated_at": "2026-09-15T…"
  }
  ```

- 无文件 / 损坏 / v1 旧格式 `{"model": x}` → 回退**默认注册表**（上例 qwen-plus 单条），
  行为与旧版一致（向后兼容）。
- 生效优先级：**注册表 `current` > 环境变量 `SLIRN_LLM_MODEL`（仅无配置文件时覆盖默认
  模型 id）> 默认 qwen-plus**。
- 字段校验（拒绝保存并提示）：
  - 模型名 `^[A-Za-z0-9][A-Za-z0-9._:\-]{0,63}$`（覆盖 `qwen-plus` / `deepseek-chat` / `gpt-4o-mini` 等命名）；
  - 环境变量名 `^[A-Za-z_][A-Za-z0-9_]{0,63}$`；
  - Base URL：http(s) 且 ≤300 字符，尾斜杠归一去除；
  - 厂商留空 → 存为「未填写厂商」。
- CRUD 语义：**add 不改变 current**（注册表从空添加首个时除外，自动成为 current）；
  **remove 当前模型 → 自动切到剩余第一个**；删除唯一模型 → 注册表为空，修订入口提示
  「未注册任何大模型 — 请先点顶栏 ⚙️ 添加模型」；重复 id → 拒绝（提示先删除再添加）。

### 2.2 OpenAI 兼容调用层（多厂商统一）

- 统一协议：`POST {base_url}/chat/completions` + `Authorization: Bearer $<api_key_env>`，
  请求体 `{model, messages, stream:false}`（`httpx` — gradio 既有依赖，不新增）。
  凡 OpenAI 兼容网关（百炼/DeepSeek/月之暗面/OpenRouter/vLLM 自建等）均可注册即用。
- Key 从 `entry.api_key_env` 指定的系统环境变量读取；未设置 →
  `未配置环境变量 XXX（厂商 的 API Key）`（不发起请求，直接可读报错）。
- entry 全链路传播：`app.py` 启动修订分析时 `get_current_entry()` 解析当前模型 →
  `start_job(..., entry=entry)` 该次分析全程使用 → 写入 revision.json meta
  `{model, provider}` 留痕（建议是用哪个模型生成的，一眼可查）。
- 错误可读化（继承 REQ-007 格式）：非 200 → `厂商（模型）[code] 中文说明：msg（request_id=…）`；
  常见码映射中文（Arrearage→欠费、InvalidApiKey、Throttling→限流、model_not_found 等）。
- 稳定性：瞬时网络/服务端错误退避重试（2s/4s）；`finish_reason=length` 截断 →
  ValueError → 上层批减半；确定性业务错误（欠费/Key 无效等）直接终止 job 不浪费调用。

### 2.3 界面（顶栏 ⚙️ 弹窗）

- **已注册模型列表**：每条含 ⭐（当前，紫色高亮卡片）、模型名、厂商、base_url、
  `<code>环境变量名</code>`、Key 状态徽章（`✅ Key 已配置` / `❌ 未配置 XXX`）、
  操作按钮 `使用`（当前项禁用）/`测试`/`删除`。
- **添加模型表单**：4 个输入框（模型名/厂商/Base URL/Key 环境变量名）+ 灰底提示
  （OpenAI 兼容 `{Base URL}/chat/completions`；**界面不存储 Key，只登记环境变量名**）。
- **测试**：单次极小真实调用（retries=0，回复「正常」二字）→ 内联
  `✅ 模型可用（X · 耗时 Ns · 回复「正常」）` 或 `❌ + 可读错误`。
  跑长视频分析前先 1 秒确认模型/Key/余额。
- 修订面板提示行显示当前生效模型：状态 2 显示当前模型，状态 3 显示建议生成时的模型（meta）。

### 2.4 API

| 端点 | 方法 | 作用 |
|---|---|---|
| `/slirn/api/llm_config` | GET | `{models:[…+key_present], current}`（弹窗初始化） |
| `/slirn/api/llm_config/add` | POST | `{id,provider,base_url,api_key_env}` → 校验+落盘 → 列表回传 |
| `/slirn/api/llm_config/remove` | POST | `{id}` → 删除（当前项自动切换） |
| `/slirn/api/llm_config/use` | POST | `{id}` → 切换 current |
| `/slirn/api/llm_test` | POST | `{id}` → 该模型真实连通性测试（ok/耗时/回复 或可读错误） |

## 3. 验收标准（v2）

- [x] AC-1 无配置文件 → 默认注册表（qwen-plus），各条目 `key_present` 如实反映环境变量
- [x] AC-2 动态添加模型（4 项信息）→ 列表即时出现且**不改变当前模型**；添加后表单清空
- [x] AC-3 「使用」切换当前模型（⭐/高亮/禁用态联动）；字幕修订分析使用该模型，
  revision.json meta 记录其 id 与 provider
- [x] AC-4 「删除」当前模型 → 自动切到剩余第一个；删光全部 → 修订入口给出
  「先添加模型」引导提示
- [x] AC-5 API Key 永不出现在界面与配置文件；环境变量未配置 → 可读错误（含变量名+厂商）
- [x] AC-6 多厂商真实连通验证：DeepSeek 调用成功 / 百炼欠费错误可读（同一调用层）
- [x] AC-7 非法输入全拒绝：坏模型名/坏 Base URL/坏环境变量名/重复 id/未注册 id 的使用与删除
- [x] AC-8 pytest 全量通过 + ruff 0 错 + API E2E + 浏览器实测（含截图）

## 4. 验证记录（2026-09-15 ~ 09-16）

- **单测**：`tests/test_llm_config.py`（默认回退/key_present/旧格式忽略/损坏回退/env 兼容/
  CRUD 往返/删空/首注册成 current/重复/未注册/不存在/三类非法字段参数化/归一化）+
  `tests/test_revision.py` 更新（entry 参数化调用层：业务错误可读、缺环境变量可读、
  限流重试后成功、截断不重试、start_job meta 记录模型与厂商）→ **pytest 125 passed**；ruff 0 错。
- **API E2E**（`work/REQ-20260915-008-llm-config/_e2e_test.py`，服务 7862）：
  GET 默认（qwen-plus，key_present=true）→ 3 种非法添加拒绝且不落盘 → add deepseek-chat
  （本机 DEEPSEEK_API_KEY 已配置）→ 重复 id 拒绝 → use 切换/未知拒绝 →
  **llm_test deepseek-chat 真实调用成功（0.89s，回复「正常」）** → demo-no-key
  （`UNITTEST_NO_SUCH_KEY`）→ 可读错误含变量名+厂商 → qwen-plus →
  `[Arrearage] 账户欠费…` 可读错误（含 request_id）→ 清理恢复默认。
- **浏览器实测**（`_browser_test.py`，CDP 1400px）：REQ-009 卡片测量（每卡 395px ≥340，
  4 按钮一行、文本单行）→ ⚙️ 弹窗：初始列表/当前禁用态 → 添加 deepseek-chat（表单 4 项，
  列表刷新+表单清空+current 不变）→ 使用切换（⭐/禁用态移动）→ 测试 deepseek-chat
  `✅ 模型可用（deepseek-chat · 耗时 0.6s · 回复「正常」）` → 测试 qwen-plus 欠费可读 →
  删除 deepseek-chat 自动切回 qwen-plus → 修订面板 hint 显示当前模型。
  截图 `_shot_llm_registry.png` / `_shot_llm_settings.png`（弹窗视觉复核：列表高亮/徽章/表单无错位）。
- 提交：slirn-standalone `chore(gitignore): 忽略 config/ 运行时配置目录`（d9bb75b，子模块 bump）+
  FunClip-main `feat(home): 动态多厂商模型注册+当前模型切换 (REQ-20260915-008)`。

## 5. 非目标

- ❌ 界面编辑/存储 API Key（只登记环境变量名 — 用户明确要求 Key 从系统环境变量读取）
- ❌ 模型参数调节（temperature / max_tokens 等，走默认值）
- ❌ 多模型并行/按阶段配不同模型（所有 LLM 功能共用一个「当前模型」）
- ❌ 不改 funclip/ 上游

## 6. v1 → v2 演进说明

v1（预设列表 datalist + 单模型名配置）实现后，用户重定义为**动态注册表**。v2 覆盖实现；
v1 期间落盘的 `{"model": x}` 旧格式按兼容处理（忽略 → 回退默认注册表），
`SLIRN_LLM_MODEL` 环境变量覆盖能力保留（仅作用于无配置文件时的默认模型 id）。
