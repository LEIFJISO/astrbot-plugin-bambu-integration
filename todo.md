# 推送系统待办

## v1.4.8 ~ v1.4.13 (完成)

- [x] 固件获取修复（product_name fallback, update_firmware_info KeyError）
- [x] 双喷嘴支持（id=0 右/ id=1 左互通）
- [x] 剩余时间单位修复（双喷嘴机型用分钟）
- [x] 增量合并缺失字段（firmware_version, is_dual_nozzle, nozzle_temper_left, nozzle_target_left）
- [x] 维护任务程序化投种 (_seed_defaults)
- [x] 全面校准任务（500h）
- [x] 风扇百分比 / HMS 格式化 / 层进度 / AMS 湿度百分比

## v1.5.0 (完成)

- [x] `push.mode` 新增 `"native+log"` — 发送后注入对话上下文
- [x] `_inject_to_conversation(umo, user_text, assistant_text)` 方法
- [x] AI Push 人格注入：从 persona_manager 读取 system_prompt
- [x] AI Push 后也注入对话上下文
- [x] debug_log 默认关闭

## v1.6.0 (远期)

### 完整 Agent 模式
- 研究如何在不持有 `AstrMessageEvent` 的后台场景中构造 Agent 上下文
- 让 AI 推送时可以调用 `bambu_printer_status` 等工具

