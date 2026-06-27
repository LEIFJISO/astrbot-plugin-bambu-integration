# PrintSphere Lite - 拓竹 MQTT 消息格式完整参考

> 综合参考：联网搜索 (HA-BambuLab 代码 + H2D/P1P 实测 pushall) + PrintSphere Lite 代码库 (`server.js`/`VERSIONS.md`)
> 生成日期: 2026-06-26

---

## 1. MQTT 连接参数

### 1.1 Broker 地址

| 区域 | MQTT Broker | API Base | 端口 |
|------|-------------|----------|------|
| 中国 (`cn`) | `cn.mqtt.bambulab.com` | `https://api.bambulab.cn` | **8883** (TLS) |
| 国际 (`global`) | `us.mqtt.bambulab.com` | `https://api.bambulab.com` | **8883** (TLS) |

> 注意：ESP 的 MQTT 端口是 **8883** (标准 MQTT TLS)，不是 1883。
> 参考：`ha-bambulab/bambu_client.py:314` — `self._port = 8883`

### 1.2 认证凭证

| 参数 | 值 | 来源 |
|------|-----|------|
| username | `u_<bambu_user_id>` (如 `u_123456789`) | 从 Bambu API `/v1/user-service/my/profile` 等接口获取 uid，前缀 `u_` |
| password | Bambu Cloud `access_token` | 短信/邮箱验证码登录 API 返回 |
| client_id | 任意 UUID 格式字符串 | 无严格格式要求，HA 用 `ha-bambulab-<uuid>` |
| 协议版本 | MQTT 3.1.1 (v311) | `paho.mqtt.client` 默认 |
| Keepalive | 5 秒 | 维持长连接的心跳间隔 |

### 1.3 TLS 配置

- **启用 TLS**，使用系统默认 CA 证书验证 Bambu 服务器
- 局域网模式下需要额外加载 Bambu 自签名证书（HA 项目包含 `bambu.cert` 等）
- ESP8266 端 TLS 配置由固件内置（源码未公开）

### 1.4 相关代码位置 (PrintSphere Lite `server.js`)

```js
// Line 579-581: MQTT broker host
function mqttHost(region) {
  return region === "global" ? "us.mqtt.bambulab.com" : "cn.mqtt.bambulab.com";
}

// Line 722-744: 获取 MQTT username (uid → u_uid)
async function refreshMqttUsername() { /* 调用 /v1/user-service/my/profile 等 */ }

// Line 1518-1528: 下发到 ESP 的 MQTT 配置 payload
const payload = {
  mqtt_host: mqttHost(cfg.cloud.region),
  mqtt_username: username,
  token: cfg.cloud.access_token,
  serial: cfg.printer.serial,
  // ...
};
```

---

## 2. MQTT Topic 格式

### 2.1 订阅 (Subscribe) — 接收打印机上报

```
device/{serial}/report
```

打印机通过此 topic 向云端报告状态。云端 MQTT Broker 将此 topic 的消息推送给已登录的客户端（ESP / HA / Bambu Studio 等）。

**ESP 实际订阅此 topic**，从中解析显示屏需要的字段。

### 2.2 发布 (Publish) — 向打印机发送命令

```
device/{serial}/request
```

payload 为 JSON 字符串，包含 command 字段。

**常用命令：**

| 命令 | JSON Payload | 说明 |
|------|-------------|------|
| 请求全量状态 | `{"pushing": {"sequence_id": "0", "command": "pushall"}}` | 打印机推送完整 pushall 快照 |
| 请求版本信息 | `{"info": {"sequence_id": "0", "command": "get_version"}}` | 打印机返回固件版本信息 |
| 启动状态推送 | `{"pushing": {"sequence_id": "0", "command": "start"}}` | 开始增量推送（watchdog 恢复时） |

> 参考：`ha-bambulab/commands.py` 中 `PUSH_ALL`、`GET_VERSION`、`START_PUSH` 定义

### 2.3 云事件 topic (Bambu Cloud 特有)

当打印机通过云端连接时，MQTT 消息可能包含 `event` 字段用于连接/断开通知：
```json
{
  "event": {
    "event": "client.connected"    // 或 "client.disconnected"
  }
}
```

---

## 3. pushall 消息完整 JSON 结构

以下是打印机返回的 `pushall` 消息的完整字段结构，基于 H2D 和 P1P 实测数据整理。ESP 固件解析其中的子集字段用于屏幕显示。

### 3.1 整体结构

```json
{
  "command": "push_status",     // 消息类型标识
  "msg": 0,                     // 0=全量推送, 非0=增量更新
  "sequence_id": "2021",        // 消息序列号(字符串)

  // === 以下为具体数据字段 ===
  // ... 所有字段位于顶层 JSON 对象
}
```

### 3.2 打印进度相关

| JSON Path | 类型 | 说明 | ESP 使用 |
|-----------|------|------|----------|
| `mc_percent` | int (0-100) | **主打印进度百分比** | **是** - 优先级最高 |
| `mc_remaining_time` | int | 剩余时间(秒) | **是** - 秒转分钟显示 |
| `gcode_state` | string | 打印状态 | **是** - 状态显示 |
| `layer_num` | int | 当前层数 | **是** |
| `total_layer_num` | int | 总层数 | **是** |
| `gcode_file` | string | 当前 G-code 文件名 | 否 |
| `gcode_file_prepare_percent` | string ("0"-"100") | 文件准备/下载百分比 | **否 - 已排除** |
| `mc_print_stage` | string | 打印阶段代码 | 否 |
| `mc_print_sub_stage` | int | 子阶段 | 否 |
| `mc_print_line_number` | string | 当前行号 | 否 |
| `mc_print_error_code` | string | 打印错误码 | 否 |
| `mc_stage` | int | 阶段编号 | 否 |
| `print_error` | int | 打印错误标志位 | 否 |
| `print_type` | string | 类型: cloud/local/idle/system | 否 |
| `subtask_name` | string | 子任务名/文件名 | 否 |
| `stg` | [int] | 阶段序列列表 | 否 |
| `stg_cur` | int | 当前阶段索引 | 否 |
| `mc_action` | int | 当前动作 | 否 |
| `print_gcode_action` | int | G-code 动作 | 否 |
| `print_real_action` | int | 实际动作 | 否 |
| `job_id` | string | 任务 ID | 否 |
| `task_id` | string | 主任务 ID | 否 |
| `subtask_id` | string | 子任务 ID | 否 |
| `profile_id` | string | 切片配置 ID | 否 |
| `project_id` | string | 项目 ID | 否 |
| `queue_number` | int | 排队序号 | 否 |
| `queue_total` | int | 排队总数 | 否 |
| `remain_time` | int | 剩余时间(秒) | 否 - 同 mc_remaining_time |

**gcode_state 枚举值：**

| 值 | 说明 |
|----|------|
| `IDLE` | 空闲 |
| `RUNNING` | 打印中 |
| `PAUSE` | 暂停 |
| `PREPARE` | 准备中(加热/调平/下载) |
| `FINISH` | 已完成 |
| `FAILED` | 失败 |
| `INIT` | 初始化 |

### 3.3 温度相关

#### 单喷嘴/标准机型 (P1/A1/X1 系列)

| JSON Path | 类型 | 说明 | ESP 使用 |
|-----------|------|------|----------|
| `nozzle_temper` | float | 喷嘴当前温度(°C) | **是** |
| `nozzle_target_temper` | float | 喷嘴目标温度(°C) | **是** |
| `bed_temper` | float | 热床当前温度(°C) | **是** |
| `bed_target_temper` | float | 热床目标温度(°C) | **是** |
| `chamber_temper` | float | 腔体温度(°C) | 否 |

#### H2D/双喷嘴机型 (新固件格式)

新固件使用 **高低字组合格式**：`temp = current_temp | (target_temp << 16)`

| JSON Path | 类型 | 说明 | ESP 使用 |
|-----------|------|------|----------|
| `device.bed.info.temp` | int | 热床: `current_bed | (target_bed << 16)` | **是** (兜底) |
| `device.bed_temp` | int | 热床(重复字段): 同上 | 否 |
| `device.ctc.info.temp` | int | 腔体: `current_chamber | (target_chamber << 16)` | 否 |
| `device.extruder.info[]` | array | 挤出机信息数组 | **是** (双喷嘴) |
| `device.extruder.info[].id` | int | 挤出机编号 (0=右, 1=左) | **是** |
| `device.extruder.info[].temp` | int | 温度: `current | (target << 16)` | **是** |
| `device.extruder.state` | int | 低4位=挤出机数量, 第5-8位=当前活跃 | 否 |
| `device.nozzle.info[]` | array | 喷嘴信息数组 | **是** (兜底) |
| `device.nozzle.info[].id` | int | 喷嘴编号 | **是** |
| `device.nozzle.info[].diameter` | float | 直径(mm) | 否 |
| `device.nozzle.info[].type` | string | 喷嘴类型(HS01/HH01等) | 否 |

**H2D 温度解析公式 (Python 伪代码):**
```python
# 从 device.extruder.info[id].temp 解析
raw_temp = extruder_info["temp"]
current_temp = raw_temp & 0xFFFF
target_temp = (raw_temp >> 16) & 0xFFFF

# 从 device.bed.info.temp 解析
raw_bed = bed_info["temp"]
current_bed = raw_bed & 0xFFFF
target_bed = (raw_bed >> 16) & 0xFFFF
```

**双喷嘴识别逻辑 (ESP):**
```js
// 检测 device.extruder.info[] 或 device.nozzle.info[] 数组长度 >= 2
// 且存在 id=0 和 id=1 两个条目时，判定为双喷嘴机型
// 显示时每 3 秒交替显示 L(左, id=1) / R(右, id=0) 的温度
```

### 3.4 风扇速度

| JSON Path | 类型 | 说明 |
|-----------|------|------|
| `cooling_fan_speed` | string ("0"-"15") | 零件冷却风扇(0-15→百分比) |
| `heatbreak_fan_speed` | string ("0"-"15") | 散热风扇 |
| `big_fan1_speed` | string ("0"-"15") | 辅助风扇(AUX) |
| `big_fan2_speed` | string ("0"-"15") | 腔体风扇/第二个辅助风扇 |
| `fan_gear` | int | 风扇档位 |

**风扇百分比换算:** `percentage = (int(speed) / 15) * 100`

### 3.5 速度与设备信息

| JSON Path | 类型 | 说明 |
|-----------|------|------|
| `spd_lvl` | int | 速度档位 (1=silent, 2=standard, 3=sport, 4=ludicrous) |
| `spd_mag` | int | 速度倍率(百分比) |
| `wifi_signal` | string | WiFi 信号 (如 `-53dBm`) |
| `nozzle_diameter` | string | 喷嘴直径 (如 `"0.4"`) |
| `nozzle_type` | string | 喷嘴类型 (hardened_steel/stainless_steel/brass) |
| `hw_switch_state` | int | 硬件开关状态 |
| `lifecycle` | string | 生命周期阶段 |
| `home_flag` | int | 归位标志位 (按位解析) |
| `sdcard` | bool | SD 卡状态 |
| `cali_version` | int | 校准版本 |

### 3.6 AMS 系统 (多色供料单元)

```json
{
  "ams": {
    "ams": [
      {
        "id": "0",
        "humidity": "3",        // 湿度指数(1-5)
        "temp": "26.2",         // AMS 温度(°C)
        "tray": [
          {
            "id": "0",
            "remain": 47,       // 剩余百分比
            "tag_uid": "7B45A8FF00000100",
            "tray_id_name": "A01-W2",
            "tray_info_idx": "GFA01",  // 耗材代码
            "tray_type": "PLA",
            "tray_sub_brands": "PLA Matte",
            "tray_color": "FFFFFFFF",
            "tray_weight": "1000",
            "nozzle_temp_max": "230",
            "nozzle_temp_min": "190",
            "tray_uuid": "D99401E841C14828...",
            "ctype": 0,
            "cols": ["FFFFFFFF"],
            "drying_temp": "55",
            "drying_time": "8",
            "state": 11
          }
          // ... 最多 4 个托盘槽位
        ]
      }
      // ... 最多 4 个 AMS 单元
    ],
    "ams_exist_bits": "11",
    "tray_exist_bits": "1000f",
    "tray_is_bbl_bits": "1000f",
    "tray_tar": "0",
    "tray_now": "0",
    "tray_read_done_bits": "1000f",
    "version": 7576,
    "insert_flag": true,
    "power_on_flag": false
  },
  "vt_tray": { /* 外部料盘(虚拟托盘) 结构同 tray */ },
  "ams_rfid_status": 0,
  "ams_status": 768
}
```

> PrintSphere Lite ESP 目前**未显示 AMS 相关数据**，只显示打印进度/温度/层数。

### 3.7 灯光状态

```json
{
  "lights_report": [
    {"node": "chamber_light",   "mode": "on"},
    {"node": "chamber_light2",  "mode": "off"},     // H2/X2 系列
    {"node": "work_light",      "mode": "flashing"}  // X1 系列
  ]
}
```

### 3.8 固件升级状态

```json
{
  "upgrade_state": {
    "sequence_id": 0,
    "progress": "100",
    "status": "UPGRADE_SUCCESS",    // IDLE/DOWNLOADING/UPGRADE_SUCCESS/UPGRADE_FAILED
    "new_version_state": 1,         // 0=无更新, 1=有新版本
    "new_ver_list": [
      {
        "name": "ota",
        "cur_ver": "01.07.00.00",
        "new_ver": "01.08.00.00"
      }
    ]
  }
}
```

### 3.9 其他字段

| JSON Path | 类型 | 说明 |
|-----------|------|------|
| `upload.status` | string | 云端上传状态 |
| `upload.progress` | int | 上传进度 |
| `ipcam.ipcam_dev` | string | 摄像头开关 ("1"/"0") |
| `ipcam.ipcam_record` | string | 录像开关 |
| `ipcam.timelapse` | string | 延时摄影开关 |
| `ipcam.resolution` | string | 分辨率 (1080p/720p) |
| `ipcam.rtsp_url` | string | RTSP 流地址 |
| `net.conf` | int | 网络配置状态 |
| `net.info[].ip` | int | IP 地址(整数格式) |
| `hms` | array | HMS 错误码列表 |
| `print_error` | int | 打印错误码 |
| `online.ahb` | bool | 云连接在线状态 |
| `s_obj` | array | 已跳过物体列表 |
| `force_upgrade` | bool | 强制升级标志 |

---

## 4. ESP 实际解析字段 (PrintSphere Lite 特有)

根据 `VERSIONS.md` 变更记录，ESP 固件实际解析以下字段并显示在 240x240 屏幕上：

### 4.1 ESP 使用的字段

| 显示内容 | 对应 MQTT 字段 | 备注 |
|----------|---------------|------|
| 打印进度 | `mc_percent` | 主进度，最高优先级 |
| 打印状态 | `gcode_state` | IDLE/RUNNING/PAUSE/PREPARE/FINISH/FAILED |
| 剩余时间 | `mc_remaining_time` | 秒 → 分钟 |
| 当前层数 | `layer_num` | 驼峰别名兼容 |
| 总层数 | `total_layer_num` | 驼峰别名兼容 |
| 喷嘴温度(单喷嘴) | `nozzle_temper` / `nozzle_target_temper` | 原始顶层字段 |
| 左喷嘴温度(双喷嘴) | `device.extruder.info[1].temp` 低16位 | 双喷嘴机型 |
| 右喷嘴温度(双喷嘴) | `device.extruder.info[0].temp` 低16位 | 双喷嘴机型 |
| 热床温度(标准) | `bed_temper` / `bed_target_temper` | 原始顶层字段 |
| 热床温度(H系列) | `device.bed.info.temp` 低16位 | 嵌套格式兜底 |

### 4.2 ESP 显式排除的字段 (原因：会导致进度跳变)

以下字段**不使用**，因为它们代表下载/准备阶段，不是真正打印进度：

- `download_progress` — 文件下载进度
- `model_download_progress` — 模型下载进度
- `gcode_file_prepare_percent` — G-code 准备百分比
- `prepare_percent` — 准备百分比
- `gcode_prepare_percent` — G-code 准备百分比

> 参考：`VERSIONS.md` 第16行 — "主打印进度只使用 mc_percent 和明确的打印进度字段"

### 4.3 ESP MQTT Buffer

```
MQTT_BUFFER_SIZE = 12288 字节    (从 8192 提升，用于接收 X2D/H 系列完整 pushall 首包)
```

---

## 5. 版本信息响应 (get_version)

发送 `{"info": {"command": "get_version"}}` 到 `device/{serial}/request`，
打印机从 `device/{serial}/report` 返回：

```json
{
  "info": {
    "command": "get_version",
    "sequence_id": "0",
    "module": [
      {
        "name": "ota",
        "project_name": "C11",
        "sw_ver": "01.07.00.00",
        "hw_ver": "OTA",
        "sn": "***REDACTED***",
        "flag": 0
      },
      {
        "name": "esp32",          // P1/A1 系列
        "sw_ver": "01.11.32.89"
      },
      {
        "name": "ap",             // H2D/X2D 系列
        "sw_ver": "00.00.51.98"
      },
      {
        "name": "mc",
        "sw_ver": "00.00.29.75"
      },
      {
        "name": "ams/0",          // AMS 单元
        "sw_ver": "00.00.06.49"
      }
    ]
  }
}
```

通过 `module` 中 `name === "ota"` 的 `project_name` 可识别机型。

---

## 6. 消息处理流程

### 6.1 设备连接后

```
1. MQTT 连接成功 → on_connect()
2. 订阅 device/{serial}/report
3. 发送 GET_VERSION → 获取固件信息
4. 发送 PUSH_ALL → 获取全量状态快照 (pushall)
5. 之后打印机持续推送增量状态更新 (msg != 0)
```

### 6.2 消息路由 (HA-BambuLab 参考)

```python
# bambu_client.py on_message()
json_data = json.loads(message.payload)

if "event" in json_data:
    # 云连接/断开事件
    pass
elif "print" in json_data:
    # 打印状态更新 (pushall/push_status)
    if json_data["print"]["msg"] == 0:
        # 这是全量 pushall
        pass
    device.print_update(data=json_data["print"])
elif "info" in json_data and json_data["info"]["command"] == "get_version":
    device.info_update(data=json_data["info"])
elif "system" in json_data:
    device.observe_system_command(data=json_data["system"])
```

---

## 7. 机型字段差异汇总

| 机型 | 特点 | 差异字段 |
|------|------|----------|
| **P1P/P1S** | 基础系列 | 标准顶层字段 (`nozzle_temper`, `bed_temper` 等) |
| **A1/A1 mini** | 悬臂式 | 类似 P1 系列格式 |
| **X1/Carbon** | 高端系列 | 增加 `work_light`、`ipcam.resolution`、`rtsp_url` |
| **X1E** | 企业版 | 独立固件版本、增加活性腔体加热器 |
| **P2S** | 第二代 P 系列 | 增加 `device.airduct`、`chamber_light2`、secondary AUX fan |
| **H2D/H2DPRO/H2C** | 双喷嘴系列 | `device.extruder.info[]`(高低字温度)、`device.extruder.state`、`device.nozzle.info[]`、`device.bed.info.temp`(高低字热床) |
| **H2S** | 单喷嘴 H 系列 | 同 H2D 格式但只有 1 个 extruder |
| **X2D** | 第二代 X 系列双喷嘴 | 类似 H2D 格式、`dev_model_name=N6-V2`、`dev_product_name=X2D` |

---

## 8. 数据来源标注

| 内容 | 来源 |
|------|------|
| MQTT Broker/端口/TLS 配置 | `ha-bambulab/bambu_client.py` (联网抓取) |
| Topic 格式 (`device/{serial}/report`/`request`) | `ha-bambulab/bambu_client.py` subscribe/publish 方法 |
| pushall 完整字段 (P1P) | `ha-bambulab/tests/P1P.json` (联网抓取，实际 MQTT 快照) |
| pushall 完整字段 (H2D 双喷嘴) | `ha-bambulab/tests/H2D.json` (联网抓取，实际 MQTT 快照) |
| H2D 温度解析 (高低字格式) | `ha-bambulab/models.py` Temperature.print_update() 方法 |
| gcode_state 枚举/阶段代码 | `ha-bambulab/const.py` |
| ESP 实际解析字段/排除字段 | PrintSphere Lite `VERSIONS.md` (项目内) |
| ESP MQTT buffer 大小 | PrintSphere Lite `VERSIONS.md` v0.4.50 |
| Token 获取 MQTT username | PrintSphere Lite `server.js` refreshMqttUsername() |
| 下发 ESP 配置 payload | PrintSphere Lite `server.js` pushConfigToEsp() |
