# 试点反馈规范

## 目的

本规范用于统一第三方试点工程对规范、工具、接入流程和运行时问题的反馈，避免笼统描述、丢失版本信息、无法复现和无证据结论。

## 反馈原则

- 一条反馈只描述一个问题或一个紧密相关的改进。
- 反馈必须绑定项目、commit、规范版本、工具版本和环境。
- 必须区分规范、工具、项目配置、环境、产品缺陷和误报。
- 必须提供预期、实际、复现步骤和证据。
- 敏感数据、密钥、令牌和生产数据不得进入反馈。
- 未复现或证据不足的问题应标记置信度和待确认项。

## 反馈 ID

推荐格式：

```text
FB-<project>-<YYYYMMDD>-<sequence>
```

示例：

```text
FB-order-system-20260927-001
```

反馈文件默认保存在：

```text
.agent/feedback/<feedback-id>.md
```

## 反馈类型

| 类型 | 说明 |
| --- | --- |
| `spec-gap` | 公共规范缺少必要要求 |
| `spec-conflict` | 公共规范与项目、法规或技术现实冲突 |
| `tool-bug` | CLI、Schema、执行器或流程实现错误 |
| `false-positive` | 检查误报 |
| `false-negative` | 应发现的问题没有发现 |
| `usability` | 接入复杂、错误难理解或操作成本过高 |
| `project-adapter` | 缺少技术栈、平台或环境适配 |
| `security` | 权限、密钥、数据处理、认证或审计问题 |
| `performance` | 性能、容量、资源或长时运行问题 |
| `documentation` | 文档缺失、错误或不可执行 |
| `enhancement` | 有明确价值的公共能力建议 |

## 严重级别

| 级别 | 定义 |
| --- | --- |
| `blocker` | 无法继续，或存在安全、数据、审计阻断 |
| `major` | 结果错误、需要长期绕过或无法进入验收 |
| `minor` | 有绕过方案，但持续增加成本 |
| `info` | 建议、观察或非阻塞改进 |

严重级别按实际影响确定，不按修改工作量确定。

## 必须记录的信息

- 项目名称、仓库和 commit；
- 镜像 digest（适用时）；
- Standards 版本和 sourceCommit；
- Standards CLI 版本；
- Acceptance Platform 版本和镜像 digest（适用时）；
- 执行环境；
- 反馈类型、级别、负责人和日期；
- 摘要、预期、实际和影响；
- 最小复现步骤和命令；
- 已脱敏的证据路径；
- 第三方初步判断和置信度；
- 建议方案和修复验收标准。

## 证据

优先附加：

```text
acceptance-result.json
evidence-manifest.json
standards.lock.json
acceptance.lock.json
architecture check 输出
release verify 或 SBOM 输出
失败步骤日志
最小规则或验收计划
```

证据可包含文件路径和 SHA-256。日志和截图必须脱敏。

## 处理流程

```text
第三方创建反馈
-> feedback verify 校验完整性
-> feedback submit 自动创建或更新中央 Issue
-> 分类和严重级别确认
-> 临时绕过或修复
-> 规范/工具/项目修改
-> 回归验证
-> 关闭并记录结果
```

### 状态

| 状态 | 说明 |
| --- | --- |
| `open` | 已提交，等待确认 |
| `confirmed` | 已复现并确认 |
| `planned` | 已接受并安排处理 |
| `fixed` | 修复完成，等待验证 |
| `closed` | 验证通过并关闭 |
| `rejected` | 无法复现或不属于公共范围 |
| `duplicate` | 与已有反馈重复 |

## 公共范围判断

以下情况进入公共规范或工具：

- 两个以上项目重复出现；
- 影响安全、数据、发布、审计或跨项目兼容；
- 公共 Schema、CLI、模板或执行器存在缺陷；
- 文档步骤无法按固定版本执行；
- 工具误报或漏报影响验收可信度。

只影响单个项目实现的内容，记录到项目适配或技术债，不自动升级为公共规范。

## 反馈验证

反馈提交前执行：

```powershell
standards feedback verify `
  .agent/feedback/FB-order-system-20260927-001.md
```

验证内容包括元数据、必填章节、版本和可能的敏感信息。验证通过不等于问题已确认，只代表反馈信息完整。

### 自动提交

`standards feedback submit` 会读取已验证反馈，自动创建或更新标准仓库中的 GitHub Issue，并把 Issue 编号和 URL 回写到反馈文件。

```powershell
standards feedback submit `
  .agent/feedback/FB-order-system-20260927-001.md `
  --repo slirn/Agent-Engineering-Standards
```

凭据来源优先级：

```text
GITHUB_TOKEN
GH_TOKEN
git credential fill
```

私有网络可通过 `--proxy` 指定 HTTPS 代理。重复提交同一反馈 ID 时更新已有 Issue，不创建重复 Issue。

## 关闭条件

- 问题已被确认或明确拒绝；
- 修复或替代方案已验证；
- 原复现步骤不再失败，或被明确判定为非问题；
- 规范和工具版本已记录；
- 影响的 Schema、兼容性和迁移要求已处理；
- 反馈状态和关闭证据可追踪。
