# Changelog

All notable changes to this project are documented in this file.

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
