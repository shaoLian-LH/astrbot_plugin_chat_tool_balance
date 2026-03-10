# Changelog

All notable changes to this project are documented in this file.

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
