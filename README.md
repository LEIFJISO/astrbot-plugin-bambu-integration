# astrbot-plugin-bambu-integration

拓竹 3D 打印机 AstrBot 集成插件，支持实时状态监控、智能提醒推送与 LLM 交互查询。

## 功能

- **MQTT 实时状态**：通过拓竹云 MQTT Broker 获取打印机实时状态推送
- **智能提醒**：打印完成/失败/进度节点/耗材不足/热床降温，支持去抖合并与自定义规则
- **静默时段**：按提醒类型独立设置每日静默窗口，错误提醒可不受静默限制
- **LLM 集成**：注册查询工具供 LLM 对话调用，支持 AI 风格化推送通知
- **多打印机**：同时监控账号下所有绑定打印机

## 快速开始

### 登录

方式一（推荐）—— 聊天交互登录（两步）：

```
1) /bambu login <手机号或邮箱>   发送验证码，例 /bambu login 13800138000
2) /bambu verify <验证码>       提交验证码，自动完成配置与 MQTT 连接
```

发送 `/bambu login`（不带参数）可查看登录流程引导。

方式二 —— WebUI 手动配置：

插件配置页面 → 填写 `region`、`account`、`access_token` 即可。

### 查询

```
/bambu status            # 连接状态
/bambu printers          # 打印机列表
/bambu info              # 状态简报
/bambu detail            # 详细状态
/BAMBU INFO 或/DETAIL    # LLM 对话中自然语言询问
```

### 指令列表

```
/bambu login [账号]      登录（不带参数查看引导）
/bambu verify <code>     提交验证码
/bambu logout            登出
/bambu status            连接状态
/bambu printers          打印机列表
/bambu info              状态简报
/bambu detail            详细状态
/bambu alert             提醒设置
/bambu mute              静默设置
/bambu rules             自定义规则
/bambu rule add/set/del/on/off/test  管理规则
/bambu rule vars         可用变量
/bambu counters          计数器
/bambu counter set <名称> <值>  设置计数器
/bambu maintenance       维护任务
/bambu maintenance skip <名称>  跳过下次提醒
/bambu maintenance mute <名称> <HH:MM> <HH:MM>  设置静默
/bambu help              帮助
```

### 配置

所有设置可通过 WebUI 插件配置页面可视化编辑，也可通过指令实时调整。

### 自定义规则

在 WebUI 配置页面的「自定义提醒规则」中添加，用 Python 表达式定义触发条件。

```
示例（打印完成且热床降温到 40°C）：
  条件: gcode_state == 'FINISH' and bed_temper <= 40
  消息: 打印完成，热床已降至{bed_temper:.0f}°C
  模式: edge（条件变为成立时触发一次）
```

**触发模式：**

| 模式 | 行为 |
|---|---|
| `edge`（默认） | 条件从 False → True 时触发一次，持续成立不重复 |
| `level` | 条件持续成立时，每隔冷却时间重复推送 |

**可用变量：**

| 变量 | 类型 | 说明 |
|---|---|---|
| `gcode_state` | str | IDLE / RUNNING / PAUSE / PREPARE / FINISH / FAILED |
| `mc_percent` | int | 打印进度 0-100 |
| `mc_remaining_time` | int | 剩余时间(秒) |
| `nozzle_temper` | float | 喷嘴当前温度 |
| `nozzle_target_temper` | float | 喷嘴目标温度 |
| `bed_temper` | float | 热床当前温度 |
| `bed_target_temper` | float | 热床目标温度 |
| `chamber_temper` | float | 腔体温度 |
| `layer_num` | int | 当前层数 |
| `total_layer_num` | int | 总层数 |
| `print_error` | int | 错误码(0=无错误) |
| `spd_lvl` | int | 速度档位 1-4 |
| `spd_mag` | int | 速度倍率% |
| `serial` | str | 打印机序列号 |
| `ams_lowest_remain` | float | 最低耗材余量% |
| `gcode_state_old` | str | 上一次状态（检测变化用） |
| `print_hours` | float | 累计打印小时 |
| `completion_count` | int | 打印完成次数 |
| `failure_consecutive` | int | 连续失败次数 |

### 维护任务

按打印小时或完成次数自动触发维护提醒。WebUI 配置页面的 `maintenance_tasks` 组中预设了 12 条基于 Bambu 官方指南的维护任务，用户可删改。

```
/bambu maintenance                   # 列出所有维护任务及下次提醒时间
/bambu maintenance skip <名称>       # 跳过下次提醒（提前保养后用）
/bambu maintenance mute <名称> off   # 取消静默
```

| # | 名称 | 类型 | 间隔 | 说明 |
|---|---|---|---|---|
| 1 | 工具头切刀检查 | hours | 80 | 常规耗材每 8-12 卷 |
| 2 | 打印板清洁 | hours | 100 | 保持模型粘附力 |
| 3 | XY 轴光轴润滑 | hours | 150 | 光轴表面清洁润滑 |
| 4 | 同步皮带检查 | hours | 200 | 张紧度与齿面磨损检查 |
| 5 | 主挤出机清洁 | hours | 250 | 碎屑清理 + 齿轮润滑 |
| 6 | 喷嘴检查 | hours | 300 | 孔径磨损 / 挤出不均 |
| 7 | Z 轴丝杆润滑 | hours | 450 | 螺纹清洁 + 锂基脂润滑 |
| 8 | 线缆检查 | hours | 500 | 绝缘皮磨损 / 接线松动 |
| 9 | 碳滤芯更换 | hours | 600 | 滤芯脏污 / 出风异味 |
| 10 | 全面检查 | hours | 700 | 硅胶套/喉管/滑轮/传感器/风扇/线缆 |
| 11 | 打印板涂胶 | completions | 5 | PEI 板变光滑后涂胶 |
| 12 | 擦嘴硅胶检查 | completions | 30 | 硅胶损坏/变形需换 |

**手动设置计数器**：插件无法读取打印机历史打印时长，初始值为 0。如有历史记录可手动设置：
```
/bambu counter set print_hours 520
/bambu counter set completion_count 300
```

## 依赖

- Python >= 3.10
- astrbot >= 4.5.0
