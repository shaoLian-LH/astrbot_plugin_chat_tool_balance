# Changelog

All notable changes to this project are documented in this file.

## [0.3.0] - 2026-03-11

### Added

- 新增 `services/llm_gateway/` 分层实现（ProviderResolver / CapabilityRouter / ResponsesTransport / AstrBotTransport / LLMGateway / Observability）。
- 新增 `storage/response_state_repository.py` 与 `core/response_state.db`，支持 chat 链路持久化 `previous_response_id`。
- 新增 `scheduler/summary_state_janitor.py`，在 summary 完成态清理 `response_state` 的 `(scope_id, topic_id)` 状态键。
- 新增阶段回归测试 `tests/test_44` ~ `tests/test_58`，覆盖配置契约、仓储、路由、传输、网关、主链路与可观测性。

### Changed

- 聊天主链路接入 `LLMGateway.chat_with_state_sync`，优先 Responses 并在失败时自动降级 AstrBot fallback。
- 运行时装配增加非 chat 角色网关注入：OCR、主题分类、工具意图、总结统一走 `LLMGateway.generate_once_sync` 且保持无状态。
- `SummaryExecutor` 在 `completed` 分支接入状态清理；`failed/sync_pending` 分支保持状态不删除以支持重试。
- 配置与 Schema 扩展 `features.use_responses_api`（默认 `true`），并为 `models.*` 增加 `_special: select_provider`。
- 更新 README 的 Responses 开关与 `models.*` provider 语义说明，补充最小配置示例；`requirements.txt` 新增 `openai` 依赖。

## [0.2.1] - 2026-03-10

### Fixed

- 修复 AstrBot 事件监听注册链路，`main.py` 改为官方装饰器直连，移除运行时动态解析。
- 修复消息标准化在多来源字段回退场景下的提取稳定性，并补齐图片 URL 去重与状态命令判定。
- 修复插件运行时装配职责耦合问题，将配置解析与 LivingMemory 客户端解析下沉到独立服务层。

### Changed

- 按 `.slfk/docs/PLAN.md` 拆分 `main.py`，新增 `handlers/` 与 `services/` 模块以降低入口复杂度。
- 新增 `requirements.txt` 统一运行/测试依赖声明，并补充对应契约与回归测试用例。

## [0.2.0] - 2026-03-10

### Added

- 基于 `.slfk/docs/PLAN.md` 落地 Chat Tool Balance MVP 主能力。
- 新增模块化插件架构：`pipeline/`、`storage/`、`scheduler/`、`bridge/`。
- 新增存储初始化、10 分桶路由、SQLite schema 初始化与配置护栏。
- 新增图片 OCR、工具意图识别、主题路由、短期记忆与上下文构建阶段。
- 新增 tool-first 编排、聊天降级分支与总结触发联动。
- 新增 LivingMemory v2 桥接与 `pending_sync` 重试同步机制。
- 新增覆盖主链路与降级路径的单元/集成测试集。

### Changed

- 将模板插件实现替换为 `ChatToolBalancePlugin`。
- 将插件元信息从模板默认值更新为 `astrbot_plugin_chat_tool_balance`。
