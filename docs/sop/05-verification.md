# 阶段 5：验证

**目标**：对照**最初的需求**逐条验证实现确实满足，并产出可交付的成果。

## 入口条件

- 评审通过
- 代码已合入主分支

## 产出

- 验证报告（可写在 PR 描述或 commit message）
- 交付清单

## Checklist

### 逐条验收

回到 `requirements/REQ-<id>.md` 的**每一条验收标准**，必须满足：

- [ ] 标准 1：______（写明具体怎么验证、结果）
- [ ] 标准 2：______
- [ ] ...

每条标准必须有：
- **怎么验证**（测试命令 / 手动步骤）
- **验证结果**（PASS / FAIL + 证据）
- **证据**（截图、日志、文件路径）

### 端到端

- [ ] 用真实数据跑一次完整流程
- [ ] 跑测试：`./.venv/Scripts/python.exe -m pytest tests/ -v`
- [ ] 跑 lint：`ruff check .`（如已装）

### 文档

- [ ] README 反映新功能（如适用）
- [ ] CLAUDE.md 更新（如有新 SOP 节点）
- [ ] Changelog 更新

### 部署/交付

- [ ] requirements.txt 更新（如有新依赖）
- [ ] 启动脚本测试通过
- [ ] 大改动需要写 release notes

### 收尾

- [ ] issue 关闭
- [ ] 相关 PR 合并
- [ ] 通知 stakeholder
- [ ] 归档需求文档到 `requirements/done/`

## 出口条件

- [ ] 所有验收标准 PASS
- [ ] 验证报告已记录
- [ ] 用户确认交付

## 反例

- ❌ "测试都过了"但没对照需求验收
- ❌ 只在 happy path 验证，没跑异常场景
- ❌ 验证报告没证据，全是文字描述

## 完成

🎉 5 阶段全部完成。归档 `requirements/REQ-<id>.md` 到 `requirements/done/`，结束本次工作流。
