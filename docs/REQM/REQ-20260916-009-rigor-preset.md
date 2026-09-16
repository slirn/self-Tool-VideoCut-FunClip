# REQ-20260916-009 — 自定义严谨性可载入高/中/低底稿修改并支持任一档恢复

## 需求原话

> 自定义严谨性提示词可以选择在高、中、低三种提示词基础之上去进行修改，并可以选择恢复高、中、低中的任意一种，这样可以给用户更好的参考描述

## 设计

### 底稿按钮组（载入与恢复合一）

- 自定义编辑区头部新增「底稿：」标签 + 三个胶囊按钮（`高 · 严格打磨` / `中 · 意思正确即可` / `低 · 只去严重问题`，文案取自 `RIGOR_LEVELS` 的 badge+title）。
- 点击即载入该档**完整提示词**到 textarea — 既是「在该底稿上开始修改」，也是「恢复该档」（同一组按钮，无需单独恢复入口）。
- 按钮带 title 提示「载入「…」完整提示词，可在此基础上修改」。

### 三档底稿随 HTML data 属性下发

- 容器 `#slirn-rigor-custom` 新增 `data-prompt-high/medium/low` 属性，值为 `_esc(revision_service.build_system_prompt(key))` — 与服务端**同源**（无 JS 硬编码重复，服务端改提示词自动跟随）。
- 服务端逻辑零改动：`RIGOR_LEVELS` / `build_system_prompt` / `default_custom_prompt`（= 高档）/ `resolve_system_prompt`（custom 空白回退底稿）均维持 REQ-007 原状。

### 覆盖保护 + 草稿同步

- 点底稿时若 textarea 非空且 ≠ 目标底稿 → `window.confirm('载入底稿「<按钮文本」会覆盖当前编辑内容。确定继续？')`（confirm 为仓库既有模式，见 app.py 2435/2686/2712）；确认后填充。
- 载入即 `localStorage.setItem('slirnRevCustomPrompt', text)` 草稿同步 + toast「已载入底稿「<名>」，可在此基础上修改」。
- 按钮显示名取 `target.textContent.trim()`，避免 JS 里重复维护名称映射。

### 预填与默认

- `syncRigorCustomUI` 首次展开预填 localStorage 草稿；无草稿用 `data-prompt-high` — **默认底稿仍 = 高档**，与服务端 `default_custom_prompt()` 回退行为一致。
- 旧 `data-default-prompt` 属性与 `rigor-prompt-reset` 按钮退役（被底稿组替代）。

### 前端文案

- 自定义卡 desc →「可在高/中/低任一底稿基础上修改修订标准，可附加自有规则」。
- 编辑区 tip →「点「底稿：高/中/低」载入对应级别的完整提示词（会覆盖当前编辑内容），给修改提供参考、也可一键恢复任一档；请保留「输出要求」中的 JSON 格式部分……」。

## 验收标准

1. 自定义严谨性编辑区提供高/中/低三个底稿按钮，点击即载入该档完整提示词。✅
2. 在任一底稿基础上直接修改，作为自定义提示词提交（REQ-007 既有行为不变）。✅
3. 可随时恢复高/中/低任意一档（再次点对应按钮即可，载入与恢复合一）。✅
4. 覆盖已有编辑内容前有确认提示，防误触丢失。✅
5. 三档底稿与服务端 `build_system_prompt` 同源（data 属性下发，无 JS 重复维护）。✅
6. 无草稿时默认底稿为高档（与 `default_custom_prompt` 回退一致）。✅

## 验证记录

- `ruff check` clean；`pytest tests/ -v` → **156 passed**（test_revision 更新断言：底稿按钮 ×3（data-preset high/medium/low）、data-prompt-* 三属性存在、旧 `data-default-prompt` 不存在、placeholder 文案）。
- 浏览器实测（CDP headless，任务 20260915-002 **只读**）7 步全绿：①严谨性四档卡 + 自定义说明含三档底稿 + 旧「恢复默认提示词」下线 ②选自定义→编辑区展开、三档底稿互不相同（810/846/807 字）、预填 = 高档 ③④⑤依次点中/低/高 → textarea = 该档底稿 + 草稿同步 ⑥截图 ⑦零 revise_subtitle/save_revision/build_cutlist 请求（**用户数据零改动、零 LLM 消耗**）。
- 视觉复核（截图 + 视觉模型，字幕修订面板 + 自定义编辑区展开态）6/6 通过：标题/「底稿：」+ 三胶囊按钮文字完整无截断/自定义卡选中态（蓝描边）+ desc 文案/textarea 预填完整提示词/tip 文案/无重叠溢出。

## 非目标

- 服务端严谨性回退逻辑变更（`resolve_system_prompt` 维持 REQ-007 行为：custom 空白回退高档底稿）。
- 底稿内容在服务端的可见性编辑（`RIGOR_LEVELS` 文案调整属服务端改动，data 属性自动跟随）。
- 多份自定义草稿管理（仍单一草稿 slirnRevCustomPrompt）。

## 关联

- 前置：REQ-20260916-007（自定义严谨性档 + 默认提示词可编辑 — 本 REQ 在其编辑区上增加底稿组）
- 提交：`feat(home): 自定义严谨性可载入高/中/低底稿修改并支持任一档恢复 (REQ-20260916-009)`（funclip-main）
