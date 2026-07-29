# 项目状态摘要

## 版本历史

| 版本 | 日期 | 主题 | 主要变更 |
|---|---|---|---|---|---|
| v1.5.7 | — | 日志静默 | 注释高频推送 DEBUG 日志、自定义提醒 once 模式、cooldown 去重 |
| v1.5.6 | — | AI Push | AI 推送人格注入修复、对话上下文注入、参数传递修正 |
| v1.5.5 | — | Bug修复 | 版本号修复、debug_log、native+log、异常日志、HMS 去重重做、增量字段修复、cooldown 守卫 |
| v1.5.4 | 2026-07-14 | Bug修复 | H2D 增量温度继承修复、监控脚本增强 |
| v1.5.3 | 2026-07-13 | 数据 | HMS 错误码数据库 (EN/ZH 4998条)、AMS 同步误判修复 |
| v1.5.2 | 2026-07-12 | Bug修复 | AI Push 人格注入修复 (get_default_persona_v3) |
| v1.5.1 | 2026-07-10 | 维护 | 维护任务周期调整、辅助挤出机/摄像头/耗材缓冲器/运动精度校准 |
| v1.5.0 | 2026-07-10 | 推送 | native+log 模式、AI 人格注入、对话上下文注入 |
| v1.4.x | 前期 | Bug修复 | 增量合并、双喷嘴、时间单位、HMS 格式化 |

## 当前稳定版本: v1.5.4

### 核心功能
- **拓竹云登录**：短信/邮箱验证码 + token 持久化
- **MQTT 实时连接**：paho-mqtt, TLS CERT_NONE, PROTOCOL_TLS
- **多打印机监视**：X2D/H2D/P1/A1 推送给合的完整状态解析
- **三推流** Native 格式化 / Native+Log(对话注入) / AI Push(人格注入)
- **4个 LLM FunctionTool**：status/detail/list_printer/ams_status
- **17条维护任务**：17默认预设、跳过提醒、程序化投种
- **自定义提醒规则**：Python 表达式条件 + edge/level 触发 + 静默时段
- **HMS 错误码数据库**：EN/ZH 4988条，来自 ha-bambulab
- **WebUI 配置**：_conf_schema.json 完整配置页

### 当前调试状态
- 推送链路: main → plugin 日志可见 (logger 已修复)
- debug_log 开关: **无效** (v1.3.2 由 AstrBot logger 替代 logging.getLogger)
- 监控脚本: test_mqtt.py --monitor 可用
- pushall_dump.json: 存在 (10KB X2D 运行中快照)

## 技术栈
- **Runtime**: Python 3.10+ / AstrBot 4.5+
- **MQTT**: paho-mqtt 2.x (替代 asyncio-mqtt 后)
- **HTTP**: aiohttp
- **数据**: gzip 压缩 HMS 数据库、JSON 持久化

## 关键设计决策记录
1. **温度通道**: 标准字段 vs H2D 嵌套格式 (右 nozzle=id=0,左 nozzle=id=1)
2. **X2D mc_remaining_time**: 单位为分钟，非秒
3. **增量推送处理**: is_dual_nozzle 标记防覆盖，merge 保留旧值
4. **AMS sync 误判**: 要求 RUNNING→FINISH 过渡 (非 IDLE/FINISH→FINISH)
5. **HMS 解码**: 16字符 hex 键 (attr_hi+attr_lo+code_hi+code_lo)
6. **热端检测**: 工程材料启动暂停 + 用户 /bambu confirm hotend 继续
7. **X2D 增量 pushall 行为**: msg=1 报文仅含 `{"msg": 1}` 或少量字段，不含 `bed_temper`/`gcode_state` 等关键字段（2026-07-24 采集器确认）
8. **HMS 固件残留**: X2D msg=0 全量 pushall 永远携带 2 个已排除的 HMS 码，msg=1 增量永远携带 `hms: []`，导致每 20 秒出现/消失循环

## 已知遗留问题
- [ ] debug_log 开关无效 (v1.5.5)
- [ ] /bambu status 硬编码显示 v1.4.1 (v1.5.5)
- [ ] HMS 去重策略需重做：改为基于 msg=0 全局追踪 (v1.5.5)
- [ ] 增量 pushall 字段缺失导致 bed_temper=0 / gcode_state="" 误判 (v1.5.5)
- [ ] AI Push `get_persona()`/`get_default_persona_v3()` 缺少 `await` (v1.5.6)
- [ ] AI Push `system_prompt` 错误拼接到 prompt 而非独立参数传递 (v1.5.6)
- [ ] AI Push 缺少对话上下文 `contexts` 参数 (v1.5.6)
- [ ] maintenance done 命令未实现 (v1.6.0)
- [ ] 活跃耗材显示未实现 (v1.6.0)
- [ ] 日历时间计数器未实现 (v1.6.0)
- [ ] 条件系统未实现 (v1.6.0)
- [ ] 耗材用量跟踪未实现 (v1.6.0)
- [ ] 热端兼容性检测未实现 (v1.7.0)
- [ ] 暂停/恢复打印命令未实现 (v1.7.0)
- [ ] 湿度提醒未实现 (v1.7.0)
- [ ] 米家数据源联动未实现 (v1.7.0)
- [ ] 涂胶提醒未实现 (v1.7.0)
- [ ] AI 管理工具未实现 (v1.8.0)
- [ ] AI 远程控制打印机未实现 (v1.8.0)
- [ ] 耗材管理与使用追踪未实现 (v1.8.0)
- [ ] AI 发起打印未实现 (v1.9.0)
- [ ] 打印板自动检测未实现 (v1.9.0)
- [ ] 夜间自动校准未实现 (v1.9.0)

## 版本规划

| 版本 | 主题 | 功能 |
|------|------|------|
| v1.5.5 | 小修小补 | 版本号修复、debug_log 修复、native+log 修复、异常日志、HMS 去重重做、增量字段修复、cooldown 守卫 |
| v1.5.6 | AI Push 修复 | 人格注入 await 修复、system_prompt 参数修正、对话上下文注入、API 签名确认 |
| v1.6.0 | 维护体系核心 | 日历计数器、条件系统、维护确认、维护重置命令、耗材追踪、活跃耗材显示 |
| v1.7.0 | 环境与安全 | 热端兼容性检测、MQTT 命令通道、湿度提醒、米家联动、涂胶提醒 |
| v1.8.0 | AI 赋能 | AI 管理工具、AI 远程控制打印机、耗材管理与使用追踪（独立 Webpage 可能单开项目） |
| v1.9.0 | 高级自动化 | AI 发起打印、打印板自动检测、夜间自动校准 |

## 项目文件清单
```
├── main.py              # 插件入口 + 命令处理器
├── metadata.yaml         # 版本 v1.5.5
├── _conf_schema.json    # WebUI 配置
├── requirements.txt      # aiohttp, paho-mqtt, pyyaml
├── cloud_api.py          # 拓竹云 API (登录/MQTT凭据/绑定)
├── mqtt_client.py        # MQTT 连接/订阅/消息路由
├── printer_manager.py    # 状态解析/增量合并/双喷嘴
├── alert_engine.py       # 事件评估/去抖队列/三推流分发
├── shared.py             # 全局对象引用桥接
├── hms_codes.py          # HMS 错误码解码器
├── hms_error_text/       # EN/ZH 压缩 HMS 数据库 (128KB)
│   ├── hms_en.json.gz
│   └── hms_zh_cn.json.gz
├── tools/
│   ├── __init__.py
│   └── printer_tools.py  # 4个 LLM FunctionTool
├── test_mqtt.py           # MQTT 监控诊断脚本 (gitignored)
├── todo.md                # 版本计划
├── pushall_dump.json     # X2D 全量推送参考数据
└── README.md

数据文件:
├── data/bambu_state.json  # 计数器 + maintenance_triggers (运行时生成)
└── data/astrbot_plugin_bambu_integration_config.json  # 用户配置 (AstrBot 管理)
```

## 下一会话启动建议
1. 开始 v1.5.5：修复 `/bambu status` 版本号硬编码 + debug_log 开关
2. 进入 v1.6.0：日历计数器 → 条件系统 → 维护确认 → 耗材追踪 → 活跃耗材显示
3. 如需调试，检查 pushall_dump.json 获取最新 X2D 数据结构
4. 使用 test_mqtt.py --monitor 诊断 MQTT 层问题
