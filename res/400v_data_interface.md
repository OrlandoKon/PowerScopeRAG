# 400V低压配电网停电原因分析——数据接口文档

> 本文档基于《数据需求清单》，定义400V停电原因分析系统与各源数据系统之间的数据接口规范，包括接口路径、请求参数、响应字段、数据源系统、更新频率和使用说明，为智能体数据拉取模块和后端数据服务对接提供标准。

---

## 一、文档说明

### 1.1 接口分类

| 接口类别 | 数据来源系统 | 接口数量 | 主要用途 |
|---------|------------|---------|---------|
| 配变监测接口 | 配变监测终端（TTU）/用电信息采集系统 | 3个 | 配变电气量、停电感知 |
| 低压拓扑接口 | 配网GIS系统 | 2个 | 台区拓扑、停电范围分析 |
| 上级电网状态接口 | 配网自动化系统（SCADA） | 2个 | 10kV系统状态查询 |
| 停电管理接口 | 停电管理系统（OMS） | 2个 | 计划停电核查 |
| 营销业务接口 | 营销业务系统 | 2个 | 欠费、用户信息 |
| 气象数据接口 | 气象数据服务 | 2个 | 天气、雷电数据 |
| 设备台账接口 | 生产管理系统（PMS） | 2个 | 设备信息、运维记录 |
| 故障抢修接口 | 抢修工单系统 | 1个 | 现场处理记录 |

### 1.2 通用约定

- **协议**：HTTP/HTTPS REST API，JSON格式请求和响应
- **字符编码**：UTF-8
- **时间格式**：ISO 8601，`yyyy-MM-dd HH:mm:ss`，时区为北京时间（UTC+8）
- **认证方式**：Bearer Token（由各源系统统一认证平台签发）
- **错误码规范**：200=成功，400=参数错误，401=未授权，404=资源不存在，500=系统错误
- **分页参数**（列表类接口）：`page_no`（页码，从1开始），`page_size`（每页条数，默认100，最大1000）

---

## 二、配变监测接口

### 接口 TM-01：查询配变实时运行数据

| 属性 | 内容 |
|------|------|
| **接口名称** | 查询配变实时运行数据 |
| **接口编号** | TM-01 |
| **接口路径** | `GET /api/ttu/trafo/realtime` |
| **数据来源** | 配变监测终端（TTU）/ 用电信息采集系统主站 |
| **更新频率** | 5分钟（主动上报），支持按需触发查询 |
| **用途说明** | 获取指定配变在指定时刻附近的实时电气量，用于判断配变运行状态、负载率、三相不平衡度、过热告警等 |

**请求参数：**

| 参数名 | 类型 | 必选 | 说明 |
|--------|------|------|------|
| trafo_id | String | 是 | 配变编号 |
| start_time | DateTime | 是 | 查询起始时刻 |
| end_time | DateTime | 是 | 查询截止时刻（与start_time间隔不超过24小时） |
| fields | String[] | 否 | 指定返回字段列表，默认返回全量字段 |

**响应字段：**

```json
{
  "code": 200,
  "data": {
    "trafo_id": "T001234",
    "records": [
      {
        "record_time": "2025-07-15 14:00:00",
        "ia": 152.3,
        "ib": 148.7,
        "ic": 153.1,
        "ua": 228.5,
        "ub": 229.1,
        "uc": 227.8,
        "i0": 0.3,
        "active_power": 98.5,
        "reactive_power": 22.1,
        "load_rate": 87.2,
        "oil_temperature": 62.5,
        "winding_temperature": null,
        "thd_current": 4.2,
        "three_phase_imbalance": 2.1
      }
    ],
    "total": 288
  }
}
```

**字段说明：**

| 字段名 | 单位 | 说明 |
|--------|------|------|
| ia / ib / ic | A | 三相电流 |
| ua / ub / uc | V | 三相电压 |
| i0 | A | 零序电流 |
| active_power | kW | 有功功率 |
| load_rate | % | 负载率（active_power / rated_capacity × 100） |
| oil_temperature | ℃ | 油浸式配变油温；干式配变返回null |
| winding_temperature | ℃ | 绕组温度（干式配变）；油浸式返回null |
| thd_current | % | 电流总谐波畸变率，若TTU不支持则返回null |
| three_phase_imbalance | % | 三相不平衡度，系统自动计算 |

---

### 接口 TM-02：查询配变告警与保护动作记录

| 属性 | 内容 |
|------|------|
| **接口名称** | 查询配变告警与保护动作记录 |
| **接口编号** | TM-02 |
| **接口路径** | `GET /api/ttu/trafo/alarm` |
| **数据来源** | 配变监测终端（TTU）/ 配网自动化系统 |
| **更新频率** | 实时推送（事件驱动），支持历史查询 |
| **用途说明** | 获取配变保护动作类型（过流、差动、零序等）、重合闸结果，判断停电是保护正确动作还是误动作 |

**请求参数：**

| 参数名 | 类型 | 必选 | 说明 |
|--------|------|------|------|
| trafo_id | String | 是 | 配变编号 |
| start_time | DateTime | 是 | 查询起始时刻 |
| end_time | DateTime | 是 | 查询截止时刻 |
| alarm_type | String | 否 | 告警类型过滤（relay_action / temperature / voltage / current），不传则返回全部 |

**响应字段：**

```json
{
  "code": 200,
  "data": {
    "trafo_id": "T001234",
    "alarms": [
      {
        "alarm_id": "ALM20250715001",
        "alarm_time": "2025-07-15 14:23:15",
        "alarm_type": "relay_action",
        "relay_action_code": "OC",
        "relay_action_desc": "过流保护动作",
        "reclose_attempt": true,
        "reclose_result": false,
        "reclose_time": "2025-07-15 14:23:45",
        "current_at_action": {"ia": 412.5, "ib": 398.2, "ic": 421.3}
      }
    ]
  }
}
```

**relay_action_code 编码说明：**

| 编码 | 说明 |
|------|------|
| OC | 过流保护（Overcurrent） |
| DIFF | 差动保护（Differential） |
| ZS | 零序保护（Zero Sequence） |
| TEMP | 温度保护 |
| OV | 过压保护 |
| UV | 欠压保护 |
| MAN | 人工操作分闸 |
| UNK | 未知原因跳闸 |

---

### 接口 TM-03：查询台区用户停电事件

| 属性 | 内容 |
|------|------|
| **接口名称** | 查询台区用户停电事件 |
| **接口编号** | TM-03 |
| **接口路径** | `GET /api/collection/outage/events` |
| **数据来源** | 用电信息采集系统主站 |
| **更新频率** | 实时（电表失电上报延迟通常<15分钟） |
| **用途说明** | 获取台区内停电用户列表、停电时刻、停电范围，判断停电是全台区停电还是局部停电，以及停电用户的相别分布 |

**请求参数：**

| 参数名 | 类型 | 必选 | 说明 |
|--------|------|------|------|
| trafo_id | String | 是 | 配变编号 |
| outage_start_time | DateTime | 是 | 停电事件起始时刻（精确到分钟） |
| time_window_minutes | Int | 否 | 以outage_start_time为中心的时间窗口（分钟），默认30 |

**响应字段：**

```json
{
  "code": 200,
  "data": {
    "trafo_id": "T001234",
    "total_user_count": 48,
    "outage_user_count": 48,
    "outage_coverage_rate": 100.0,
    "outage_users": [
      {
        "meter_id": "M0001001",
        "user_id": "U10001",
        "meter_phase": "A",
        "outage_time": "2025-07-15 14:23:16",
        "restore_time": null,
        "outage_duration_min": null
      }
    ],
    "phase_distribution": {
      "A": 16, "B": 16, "C": 16
    }
  }
}
```

---

## 三、低压拓扑接口

### 接口 GIS-01：查询台区低压拓扑与用户归属

| 属性 | 内容 |
|------|------|
| **接口名称** | 查询台区低压拓扑与用户归属 |
| **接口编号** | GIS-01 |
| **接口路径** | `GET /api/gis/lowvoltage/topology` |
| **数据来源** | 配网GIS系统 |
| **更新频率** | 按需更新（拓扑变更后24小时内同步） |
| **用途说明** | 获取台区低压网络拓扑，包括分支线路结构、用户接入点、各用户所属相别，用于分析停电范围与故障位置的对应关系 |

**请求参数：**

| 参数名 | 类型 | 必选 | 说明 |
|--------|------|------|------|
| trafo_id | String | 是 | 配变编号 |

**响应字段：**

```json
{
  "code": 200,
  "data": {
    "trafo_id": "T001234",
    "trafo_location": {"longitude": 121.48, "latitude": 31.23},
    "rated_capacity_kva": 200,
    "low_voltage_branches": [
      {
        "branch_id": "BR001",
        "branch_name": "A支线",
        "cable_type": "架空线",
        "cable_length_m": 320,
        "fault_indicator_installed": true,
        "fault_indicator_id": "FI001",
        "users": [
          {
            "meter_id": "M0001001",
            "user_id": "U10001",
            "meter_phase": "A",
            "location": {"longitude": 121.482, "latitude": 31.231}
          }
        ]
      }
    ]
  }
}
```

---

### 接口 GIS-02：查询低压线路设备空间信息

| 属性 | 内容 |
|------|------|
| **接口名称** | 查询低压线路设备空间信息 |
| **接口编号** | GIS-02 |
| **接口路径** | `GET /api/gis/lowvoltage/equipment` |
| **数据来源** | 配网GIS系统 |
| **更新频率** | 按需更新 |
| **用途说明** | 查询指定区域内低压线路、电缆路径、接线箱等的空间位置，用于与施工工单、现场勘查进行空间叠加分析，辅助判断外力破坏原因 |

**请求参数：**

| 参数名 | 类型 | 必选 | 说明 |
|--------|------|------|------|
| trafo_id | String | 是 | 配变编号 |
| buffer_radius_m | Int | 否 | 以台区为中心的查询半径（米），默认500 |
| equipment_type | String | 否 | 设备类型过滤（cable/overhead_line/junction_box），不传则返回全部 |

---

## 四、上级电网状态接口

### 接口 SC-01：查询10kV馈线开关状态与保护动作

| 属性 | 内容 |
|------|------|
| **接口名称** | 查询10kV馈线开关状态与保护动作 |
| **接口编号** | SC-01 |
| **接口路径** | `GET /api/scada/feeder/status` |
| **数据来源** | 配网自动化系统（SCADA） |
| **更新频率** | 实时（秒级） |
| **用途说明** | 查询台区所属10kV馈线的开关状态和保护动作记录，判断停电是否为上级馈线故障传导；是排查上级传导原因的首要接口 |

**请求参数：**

| 参数名 | 类型 | 必选 | 说明 |
|--------|------|------|------|
| trafo_id | String | 是 | 配变编号（系统内部自动关联所属馈线） |
| query_time | DateTime | 是 | 查询时刻 |
| time_window_minutes | Int | 否 | 以query_time为中心的时间窗口，默认60 |

**响应字段：**

```json
{
  "code": 200,
  "data": {
    "trafo_id": "T001234",
    "feeder_id": "F10KV-001",
    "feeder_name": "某变电站101馈线",
    "substation_id": "SS001",
    "feeder_breaker_status": "open",
    "feeder_breaker_last_change_time": "2025-07-15 14:23:14",
    "relay_action": {
      "action_time": "2025-07-15 14:23:14",
      "action_type": "OC",
      "action_desc": "过流保护动作",
      "reclose_result": "failed"
    },
    "same_feeder_outage_trafo_count": 13,
    "same_feeder_outage_trafo_list": ["T001234", "T001235", "T001236"]
  }
}
```

---

### 接口 SC-02：查询变电站母线及主变运行状态

| 属性 | 内容 |
|------|------|
| **接口名称** | 查询变电站母线及主变运行状态 |
| **接口编号** | SC-02 |
| **接口路径** | `GET /api/scada/substation/status` |
| **数据来源** | 配网自动化系统（SCADA）/ 调度自动化系统 |
| **更新频率** | 实时（秒级） |
| **用途说明** | 查询台区所属变电站的母线电压和主变运行状态，判断是否为变电站级别的故障传导 |

**请求参数：**

| 参数名 | 类型 | 必选 | 说明 |
|--------|------|------|------|
| substation_id | String | 是 | 变电站编号（或通过trafo_id自动关联） |
| query_time | DateTime | 是 | 查询时刻 |

**响应字段：**

```json
{
  "code": 200,
  "data": {
    "substation_id": "SS001",
    "substation_name": "某变电站",
    "bus_voltage_10kv": 10.32,
    "bus_voltage_normal_flag": true,
    "main_transformer_status": "normal",
    "dispatch_order": {
      "has_order": false,
      "order_type": null,
      "order_time": null
    }
  }
}
```

---

## 五、停电管理接口

### 接口 OMS-01：查询计划停电工单

| 属性 | 内容 |
|------|------|
| **接口名称** | 查询计划停电工单 |
| **接口编号** | OMS-01 |
| **接口路径** | `GET /api/oms/planned-outage/query` |
| **数据来源** | 停电管理系统（OMS） |
| **更新频率** | 按需（工单变更时同步），支持实时查询 |
| **用途说明** | 核查指定台区在指定时刻是否处于计划停电工单的时间范围内，是排查计划停电的直接依据 |

**请求参数：**

| 参数名 | 类型 | 必选 | 说明 |
|--------|------|------|------|
| trafo_id | String | 是 | 配变编号 |
| outage_time | DateTime | 是 | 停电时刻 |

**响应字段：**

```json
{
  "code": 200,
  "data": {
    "trafo_id": "T001234",
    "outage_time": "2025-07-15 14:23:15",
    "planned_outage_match": false,
    "matched_orders": []
  }
}
```

当 `planned_outage_match: true` 时，`matched_orders` 包含：

```json
"matched_orders": [
  {
    "order_id": "PO20250715001",
    "plan_start_time": "2025-07-15 08:00:00",
    "plan_end_time": "2025-07-15 17:00:00",
    "actual_start_time": "2025-07-15 08:05:00",
    "affected_scope": "某10kV馈线101线路检修",
    "notify_flag": true,
    "notify_channel": "短信",
    "work_type": "检修"
  }
]
```

---

### 接口 OMS-02：查询故障停电事件列表

| 属性 | 内容 |
|------|------|
| **接口名称** | 查询故障停电事件列表 |
| **接口编号** | OMS-02 |
| **接口路径** | `GET /api/oms/fault-outage/list` |
| **数据来源** | 停电管理系统（OMS）/ 故障抢修系统 |
| **更新频率** | 实时（故障发生后自动生成事件记录） |
| **用途说明** | 查询指定台区的历史停电事件列表，包含停电原因（已确认的）、停电时长等，用于建立历史案例库和对比分析 |

**请求参数：**

| 参数名 | 类型 | 必选 | 说明 |
|--------|------|------|------|
| trafo_id | String | 是 | 配变编号 |
| start_date | Date | 是 | 查询起始日期 |
| end_date | Date | 是 | 查询截止日期（与start_date间隔不超过1年） |
| page_no | Int | 否 | 页码，默认1 |
| page_size | Int | 否 | 每页条数，默认20 |

---

## 六、营销业务接口

### 接口 MKT-01：查询用户欠费与拉闸状态

| 属性 | 内容 |
|------|------|
| **接口名称** | 查询用户欠费与拉闸状态 |
| **接口编号** | MKT-01 |
| **接口路径** | `GET /api/marketing/user/arrear` |
| **数据来源** | 营销业务系统 |
| **更新频率** | 实时（缴费/欠费事件触发更新） |
| **用途说明** | 查询指定台区内所有用户的欠费状态和远程拉闸记录，判断停电是否由欠费拉闸引起 |

**请求参数：**

| 参数名 | 类型 | 必选 | 说明 |
|--------|------|------|------|
| trafo_id | String | 条件必选 | 配变编号（与user_id至少填一个） |
| user_id | String | 条件必选 | 用户编号 |
| query_time | DateTime | 否 | 查询截止时刻，默认当前时刻 |

**响应字段：**

```json
{
  "code": 200,
  "data": {
    "trafo_id": "T001234",
    "users": [
      {
        "user_id": "U10001",
        "meter_id": "M0001001",
        "user_name": "张三",
        "arrear_amount": 0.0,
        "arrear_days": 0,
        "remote_cutoff_status": "closed",
        "last_remote_cutoff_time": null,
        "last_remote_cutoff_reason": null
      }
    ]
  }
}
```

**remote_cutoff_status 说明：**

| 值 | 含义 |
|----|------|
| closed | 合闸状态（正常供电） |
| open | 已拉闸（欠费停电） |
| unknown | 状态未知（通信中断） |

---

### 接口 MKT-02：查询用户投诉与主动停电申请

| 属性 | 内容 |
|------|------|
| **接口名称** | 查询用户投诉与主动停电申请 |
| **接口编号** | MKT-02 |
| **接口路径** | `GET /api/marketing/user/complaints` |
| **数据来源** | 营销业务系统 / 95598客服系统 |
| **更新频率** | 实时 |
| **用途说明** | 查询停电时间窗口内该台区用户的投诉记录和主动申请停电记录，辅助判断用户侧原因 |

**请求参数：**

| 参数名 | 类型 | 必选 | 说明 |
|--------|------|------|------|
| trafo_id | String | 是 | 配变编号 |
| start_time | DateTime | 是 | 查询起始时刻 |
| end_time | DateTime | 是 | 查询截止时刻 |
| record_type | String | 否 | 记录类型（complaint=投诉, application=申请停电），不传则返回全部 |

---

## 七、气象数据接口

### 接口 WS-01：查询停电点附近气象数据

| 属性 | 内容 |
|------|------|
| **接口名称** | 查询停电点附近气象数据 |
| **接口编号** | WS-01 |
| **接口路径** | `GET /api/weather/station/history` |
| **数据来源** | 气象数据服务（对接国家/省级气象局API） |
| **更新频率** | 历史数据按需查询，实时数据1小时更新 |
| **用途说明** | 获取停电时刻附近的气象数据（气温、风速、降雨、天气类型），判断是否存在自然灾害诱因 |

**请求参数：**

| 参数名 | 类型 | 必选 | 说明 |
|--------|------|------|------|
| trafo_id | String | 是 | 配变编号（系统内部关联最近气象站） |
| target_time | DateTime | 是 | 目标时刻 |
| time_window_hours | Int | 否 | 时间窗口（小时），默认前后各2小时 |

**响应字段：**

```json
{
  "code": 200,
  "data": {
    "trafo_id": "T001234",
    "nearest_station_id": "WS001",
    "nearest_station_distance_km": 2.3,
    "records": [
      {
        "record_time": "2025-07-15 14:00:00",
        "air_temperature": 38.5,
        "wind_speed": 3.2,
        "wind_direction": "SE",
        "weather_condition": "雷暴",
        "rainfall_1h": 15.2,
        "rainfall_24h": 42.8,
        "humidity": 85.0
      }
    ]
  }
}
```

---

### 接口 WS-02：查询落雷记录

| 属性 | 内容 |
|------|------|
| **接口名称** | 查询落雷记录 |
| **接口编号** | WS-02 |
| **接口路径** | `GET /api/weather/lightning/query` |
| **数据来源** | 雷电定位系统（ADTD/VAISALA等） |
| **更新频率** | 实时（闪电发生后数秒内入库） |
| **用途说明** | 查询停电点附近指定范围内的落雷记录，辅助判断雷击原因 |

**请求参数：**

| 参数名 | 类型 | 必选 | 说明 |
|--------|------|------|------|
| trafo_id | String | 是 | 配变编号 |
| target_time | DateTime | 是 | 停电时刻 |
| time_window_minutes | Int | 否 | 时间窗口（分钟），默认前后各30分钟 |
| radius_km | Float | 否 | 搜索半径（千米），默认2.0 |

**响应字段：**

```json
{
  "code": 200,
  "data": {
    "trafo_location": {"longitude": 121.48, "latitude": 31.23},
    "lightning_count": 3,
    "lightning_records": [
      {
        "lightning_time": "2025-07-15 14:22:58",
        "longitude": 121.479,
        "latitude": 31.228,
        "distance_km": 0.32,
        "intensity_ka": -48.5,
        "lightning_type": "cloud_to_ground"
      }
    ]
  }
}
```

---

## 八、设备台账接口

### 接口 PMS-01：查询设备台账信息

| 属性 | 内容 |
|------|------|
| **接口名称** | 查询设备台账信息 |
| **接口编号** | PMS-01 |
| **接口路径** | `GET /api/pms/equipment/info` |
| **数据来源** | 生产管理系统（PMS） |
| **更新频率** | 按需更新（设备台账变更时同步） |
| **用途说明** | 查询配变、电缆、漏电保护器等设备的投运年限、额定参数、上次预防性试验时间，辅助判断设备老化类故障 |

**请求参数：**

| 参数名 | 类型 | 必选 | 说明 |
|--------|------|------|------|
| trafo_id | String | 是 | 配变编号 |
| equipment_type | String | 否 | 设备类型（transformer/cable/rcd/breaker），不传则返回台区所有设备 |

**响应字段：**

```json
{
  "code": 200,
  "data": {
    "trafo_id": "T001234",
    "equipments": [
      {
        "equipment_id": "EQ001",
        "equipment_type": "transformer",
        "model": "S11-200/10",
        "rated_capacity_kva": 200,
        "rated_voltage_hv": 10,
        "rated_voltage_lv": 0.4,
        "commission_date": "2010-06-15",
        "age_years": 15,
        "last_test_date": "2022-03-20",
        "last_test_result": "合格",
        "tap_changer_position": 3,
        "tap_changer_last_adjust_date": "2023-11-10"
      }
    ]
  }
}
```

---

### 接口 PMS-02：查询运维巡视与缺陷记录

| 属性 | 内容 |
|------|------|
| **接口名称** | 查询运维巡视与缺陷记录 |
| **接口编号** | PMS-02 |
| **接口路径** | `GET /api/pms/maintenance/records` |
| **数据来源** | 生产管理系统（PMS） |
| **更新频率** | 按需更新（巡视完成后录入） |
| **用途说明** | 查询台区近期运维巡视记录和缺陷登记，获取停电前是否已发现设备隐患，辅助判断故障原因 |

**请求参数：**

| 参数名 | 类型 | 必选 | 说明 |
|--------|------|------|------|
| trafo_id | String | 是 | 配变编号 |
| start_date | Date | 是 | 查询起始日期 |
| end_date | Date | 是 | 查询截止日期 |
| record_type | String | 否 | 记录类型（inspection=巡视, defect=缺陷），不传则返回全部 |

---

## 九、故障抢修接口

### 接口 RP-01：查询故障抢修工单

| 属性 | 内容 |
|------|------|
| **接口名称** | 查询故障抢修工单 |
| **接口编号** | RP-01 |
| **接口路径** | `GET /api/repair/workorder/query` |
| **数据来源** | 故障抢修工单系统 |
| **更新频率** | 实时（工单状态变更时同步） |
| **用途说明** | 获取停电对应的抢修工单信息，包括现场勘查结论、故障类型、故障位置，作为停电原因判断的现场验证依据 |

**请求参数：**

| 参数名 | 类型 | 必选 | 说明 |
|--------|------|------|------|
| trafo_id | String | 是 | 配变编号 |
| outage_time | DateTime | 是 | 停电时刻 |
| time_window_hours | Int | 否 | 时间窗口（小时），默认前后各4小时 |

**响应字段：**

```json
{
  "code": 200,
  "data": {
    "work_orders": [
      {
        "order_id": "WO20250715001",
        "order_status": "completed",
        "report_time": "2025-07-15 14:35:00",
        "arrive_time": "2025-07-15 15:10:00",
        "restore_time": "2025-07-15 17:30:00",
        "fault_type": "低压线路故障",
        "fault_location": "A支线第3基电杆处",
        "fault_cause_confirmed": "大风导致低压架空线断线",
        "repair_action": "重新架设导线约15米，紧固接头",
        "field_notes": "现场发现第3至第4基杆间导线断落，断点有风摆磨损痕迹"
      }
    ]
  }
}
```

---

## 十、接口调用建议

### 10.1 停电原因分析的推荐接口调用顺序

```
停电事件触发
    ↓
Step 1  OMS-01 → 查询计划停电工单（排除计划停电）
    ↓
Step 2  SC-01  → 查询10kV馈线状态（排查上级传导）
Step 2  SC-02  → 查询变电站母线状态（排查上级传导）
    ↓
Step 3  TM-03  → 查询台区用户停电范围（全区/局部）
Step 3  TM-01  → 查询配变停电前电气量
Step 3  TM-02  → 查询配变告警与保护动作记录
    ↓
Step 4  WS-01  → 查询气象数据（天气类原因排查）
Step 4  WS-02  → 查询落雷记录（雷击原因排查）
    ↓
Step 5  MKT-01 → 查询欠费拉闸状态（用户侧原因排查）
Step 5  MKT-02 → 查询用户投诉记录
    ↓
Step 6  PMS-01 → 查询设备台账（设备老化辅助判断）
Step 6  PMS-02 → 查询运维缺陷记录
    ↓
Step 7  RP-01  → 查询抢修工单（现场确认，获取最终结论）
```

### 10.2 接口超时与容错建议

| 接口类别 | 建议超时设置 | 失败处理策略 |
|---------|------------|------------|
| 实时类（SCADA/TTU） | 5秒 | 重试1次，失败则标记数据缺失，不阻断分析流程 |
| 历史查询类（OMS/MKT/PMS） | 15秒 | 重试1次，失败则标记数据缺失 |
| 气象/雷电类（外部接口） | 10秒 | 重试2次，失败则跳过该维度判断，在结论中注明数据不完整 |
| 抢修工单类 | 10秒 | 失败则略过，现场结论缺失时降低原因判断置信度 |
