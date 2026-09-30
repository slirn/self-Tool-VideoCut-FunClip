# 本地上传排除队列

> 用途：记录明确不进入 Git / GitHub 的本地调试产物，避免 `git status`
> 被大量浏览器缓存淹没，同时保留可复核的分类依据。
>
> 盘点时间：2026-09-30

## 排除队列

| 路径模式 | 数量 | 约占用 | 类型 | 不提交原因 | 后续处理 |
| --- | ---: | ---: | --- | --- | --- |
| `/tools/_edge_cdp_profile*/` | 5820 | 475 MB | Edge/CDP 浏览器配置与缓存 | 可由截图脚本重新生成；包含 Cookie、Cache、扩展与浏览器运行数据 | 保留本机，可从队列清空 |
| `/tools/_edge_dbg*/` | 3120 | 144 MB | Edge 调试配置与缓存 | 一次性排障产物，不包含项目源码 | 保留本机，可从队列清空 |
| `/tools/_*.py` | 3 | 13 KB | 一次性验证 / 补拍脚本 | 只服务单次验证；可复用截图能力已抽到 `take_manual_screenshots.py` | 若后续需要长期维护，改为正式工具后再移出队列 |
| `/tools/__pycache__/` | 1 | 14 KB | Python 字节码缓存 | 本地运行时生成 | 可随时删除 |
| `/docs/images/manual/_*.png` | 6 | 3.3 MB | 未引用调试图 | 未被使用说明书引用，只有中间排障价值 | 若后续引用则显式加入提交 |

合计约 8950 个文件、623 MB。上述路径已加入 `.gitignore`，不应再次出现在常规
`git status` 结果中。

## 文件级队列

| 文件 | 原因 |
| --- | --- |
| `tools/_e2e_flow_autosave.py` | 一次性浏览器 E2E 验证；依赖运行中的本机服务和示范任务 |
| `tools/_retake_manual_shots.py` | 一次性补拍脚本，截图产物已生成 |
| `tools/_retake_stage_shots.py` | 一次性补拍脚本，截图产物已生成 |
| `docs/images/manual/_dbg_finecut_viewport.png` | 未引用调试图 |
| `docs/images/manual/_dbg15_viewport.png` | 未引用调试图 |
| `docs/images/manual/_dbg17_C.png` | 未引用调试图 |
| `docs/images/manual/_dbg17_F.png` | 未引用调试图 |
| `docs/images/manual/_verify14b.png` | 未引用验证图 |
| `docs/images/manual/_verify15.png` | 未引用验证图 |

## 复核规则

- 队列条目需要长期维护时，先移动到正式 `docs/`、`tests/` 或 `tools/` 路径，
  再显式 `git add`，不要依赖默认忽略规则。
- 队列只包含本地生成物、一次性排障材料和不可复现的个人环境数据，不包含
  业务源码、正式测试、需求/设计/验证记录或使用说明书图片。
- 清理本机缓存前无需改仓库；如需清理，直接删除对应目录即可重新生成。
