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

## v1.6.0 (计划)

### AI 管理工具
注册 6 个 FunctionTool，让 AI 对话中可管理打印机配置：
- `bambu_set_alert` — 开关内置提醒类型
- `bambu_set_mute` — 设置静默时段
- `bambu_set_push_mode` — 切换推送模式
- `bambu_set_maintenance` — 修改维护任务间隔/启停
- `bambu_set_counter` — 手动设置计数器
- `bambu_add_rule` — 添加自定义提醒规则

## v1.7.0 (远期)

### 打印机远程控制
通过 MQTT 发布命令到 `device/{serial}/request`（参考 MQTT消息格式参考.md 2.2 节）：
- 发送 3MF/G-code 文件到打印机开始打印
- 暂停/恢复/停止当前打印任务

