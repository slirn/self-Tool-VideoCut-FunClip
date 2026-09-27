# Agent 项目开发入口

<!-- agent-standards:begin -->
开始开发、修 Bug、重构或评审前，必须读取：

1. `.agent/standards/collaboration.md`
2. `.agent/standards/delivery.md`
3. `.agent/standards/verification.md`
4. `.agent/standards/quality-gates.md`
5. `.agent/project-context.md`
6. `.agent/standards-profile.md`

当任务包含多项工作、需要任务编排或用户要求端到端连续执行时，还必须读取 `.agent/standards/orchestration.md`。

当任务预计持续数小时、需要恢复或要求无人值守时，必须读取 `.agent/standards/long-running.md`，确认执行器能力、授权预算与检查点方案。

当任务涉及日志、问题定位、流程追踪、运行开关或诊断页面时，还必须读取 `.agent/standards/logging.md`。

按任务内容还必须读取对应领域规范：安全或隐私读取 `security.md`；接口读取 `api.md`；配置读取 `configuration.md`；可靠性、超时、重试或降级读取 `reliability.md`；数据库或数据变更读取 `database.md`；性能读取 `performance.md`；测试读取 `testing.md`；部署或发布读取 `deployment.md`；指标、链路或告警读取 `observability.md`；依赖或供应链读取 `dependencies.md`。

先理解目标、范围、架构和验收标准，再修改代码。需要确认的内容必须短而明确；未获当前阶段批准，不得进入下一阶段。

委派子 Agent 时，必须传递上述规则、已确认范围、验收标准、修改权限和禁止事项。子 Agent 不得批准需求或扩大范围。

文件缺失、规则冲突或需求歧义时，先报告并请求澄清，不得声称已加载或自行忽略规则。

当任务涉及公共规范与项目现有规范的融合、冲突、例外或历史差距时，还必须读取 `.agent/standards/adoption.md` 和 `.agent/standards-profile.md`。

当任务涉及构建、发布、版本或安装来源时，还必须读取 `.agent/standards/release-provenance.md`。

当任务需要确定重要、跨模块或难回滚的技术方案时，还必须读取 `.agent/standards/decisions.md`。

当任务涉及生产事故、严重故障或用户影响事件时，还必须读取 `.agent/standards/incident-management.md`。

当任务产生临时妥协、规范例外、历史缺口或已知风险时，还必须读取 `.agent/standards/technical-debt.md`。

当任务涉及自动验收、阶段状态流转、独立审查、功能或安全验收、48/72 小时运行、影子或灰度验收时，还必须读取 `.agent/standards/acceptance-automation.md`、项目验收配置和 `.agent/acceptance.lock.json`。

当任务涉及 Agent 工具权限、提示词注入、模型上下文、长期记忆、运行审计、费用或资源限制时，还必须读取 `.agent/standards/agent-runtime-security.md`。

当任务涉及个人数据、敏感信息、数据导出、共享、保留、删除、测试数据或跨境处理时，还必须读取 `.agent/standards/data-governance.md`。

当任务涉及 SLO、错误预算、容量、备份恢复、RTO/RPO、容灾或降级演练时，还必须读取 `.agent/standards/slo-resilience.md`。

当任务涉及模块边界、依赖方向、循环依赖、数据所有权、ADR 落地或架构重构时，还必须读取 `.agent/standards/architecture-fitness.md`。

当任务涉及前端页面、交互、响应式布局、可访问性、国际化、前端性能或浏览器兼容时，还必须读取 `.agent/standards/frontend-quality.md`。

当试点工程需要反馈规范缺口、工具缺陷、误报、接入困难、安全或性能问题时，必须读取 `.agent/standards/pilot-feedback.md`，使用 `standards feedback new` 创建，并在提交前运行 `standards feedback verify`。
<!-- agent-standards:end -->
