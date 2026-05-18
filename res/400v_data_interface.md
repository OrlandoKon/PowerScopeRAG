# 400V低压配电网停电原因分析——数据接口文档（雄安新区场景）

> **场景说明**：本文档面向雄安新区400V停电原因分析系统，在通用接口基础上新增四类雄安特有接口：综合管廊监控系统接口、充电桩管理系统接口、分布式能源管理系统接口、建设工程施工管理接口。已删除或注明架空线相关数据的获取接口（风速对架空线断线的判断在雄安不适用）。

---

## 一、文档说明

### 1.1 接口分类总览

| 类别 | 接口编号 | 数据来源系统 | 雄安特有 |
|------|---------|------------|---------|
| 配变监测接口 | TM-01、TM-02、TM-03 | TTU / 用电信息采集系统 | — |
| 低压拓扑接口 | GIS-01、GIS-02 | 配网GIS系统 | — |
| 上级电网状态接口 | SC-01、SC-02 | 配网自动化系统（SCADA） | — |
| 停电管理接口 | OMS-01、OMS-02 | 停电管理系统（OMS） | — |
| 营销业务接口 | MKT-01、MKT-02 | 营销业务系统 | — |
| 气象数据接口 | WS-01、WS-02 | 气象数据服务 / 雷电定位系统 | — |
| 设备台账接口 | PMS-01、PMS-02 | 生产管理系统（PMS） | — |
| 故障抢修接口 | RP-01 | 故障抢修工单系统 | — |
| **综合管廊监控接口** | **PG-01、PG-02** | **综合管廊监控管理系统** | ⚡ 雄安特有 |
| **充电桩管理接口** | **EV-01、EV-02** | **充电桩管理系统（EVCS）** | ⚡ 雄安特有 |
| **分布式能源管理接口** | **DG-01** | **分布式能源管理系统（DEMS）** | ⚡ 雄安特有 |
| **建设工程施工管理接口** | **CONST-01** | **建设工程施工管理系统** | ⚡ 雄安特有 |

### 1.2 通用约定

- **协议**：HTTP/HTTPS REST API，JSON格式
- **字符编码**：UTF-8
- **时间格式**：ISO 8601，`yyyy-MM-dd HH:mm:ss`，时区为北京时间（UTC+8）
- **认证方式**：Bearer Token（由雄安新区统一数字平台统一认证签发）
- **错误码规范**：200=成功，400=参数错误，401=未授权，404=资源不存在，500=系统错误
- **分页参数**：`page_no`（页码，从1开始），`page_size`（每页条数，默认100，最大1000）

---

## 二、配变监测接口

### 接口 TM-01：查询配变实时运行数据

| 属性 | 内容 |
|------|------|
| **接口编号** | TM-01 |
| **接口路径** | `GET /api/ttu/trafo/realtime` |
| **数据来源** | 配变监测终端（TTU）/ 用电信息采集系统主站 |
| **更新频率** | 5分钟（主动上报），支持按需触发查询 |
| **用途说明** | 获取配变实时电气量，用于判断配变运行状态、负载率、三相不平衡度、过热告警、有功功率方向（光伏逆流判断）等 |

**请求参数：**

| 参数名 | 类型 | 必选 | 说明 |
|--------|------|------|------|
| trafo_id | String | 是 | 配变编号 |
| start_time | DateTime | 是 | 查询起始时刻 |
| end_time | DateTime | 是 | 查询截止时刻（与start_time间隔不超过24小时） |
| fields | String[] | 否 | 指定返回字段列表，默认返回全量字段 |

**响应字段（雄安版新增 `active_power_direction`）：**

```json
{
  "code": 200,
  "data": {
    "trafo_id": "XA-T001234",
    "records": [
      {
        "record_time": "2025-07-15 14:00:00",
        "ia": 152.3, "ib": 148.7, "ic": 153.1,
        "ua": 228.5, "ub": 229.1, "uc": 227.8,
        "i0": 0.3,
        "active_power": -12.5,
        "active_power_direction": "reverse",
        "load_rate": 87.2,
        "oil_temperature": 62.5,
        "thd_current": 8.7,
        "three_phase_imbalance": 2.1
      }
    ]
  }
}
```

> **雄安说明**：`active_power` 为负值时表示存在光伏逆流；`thd_current` 重点关注充电桩高峰期（晚间）的谐波水平。

---

### 接口 TM-02：查询配变告警与保护动作记录

| 属性 | 内容 |
|------|------|
| **接口编号** | TM-02 |
| **接口路径** | `GET /api/ttu/trafo/alarm` |
| **数据来源** | 配变监测终端（TTU）/ 配网自动化系统 |
| **更新频率** | 实时推送（事件驱动） |
| **用途说明** | 获取配变保护动作类型（过流、差动、零序等）、重合闸结果，判断停电是保护正确动作还是误动作 |

**请求参数：**

| 参数名 | 类型 | 必选 | 说明 |
|--------|------|------|------|
| trafo_id | String | 是 | 配变编号 |
| start_time | DateTime | 是 | 查询起始时刻 |
| end_time | DateTime | 是 | 查询截止时刻 |
| alarm_type | String | 否 | 告警类型过滤（relay_action/temperature/voltage/current） |

**响应字段：**

```json
{
  "code": 200,
  "data": {
    "trafo_id": "XA-T001234",
    "alarms": [
      {
        "alarm_id": "XA-ALM20250715001",
        "alarm_time": "2025-07-15 14:23:15",
        "alarm_type": "relay_action",
        "relay_action_code": "OC",
        "relay_action_desc": "过流保护动作",
        "reclose_attempt": true,
        "reclose_result": false,
        "current_at_action": {"ia": 412.5, "ib": 398.2, "ic": 421.3}
      }
    ]
  }
}
```

**relay_action_code 编码说明：**

| 编码 | 说明 |
|------|------|
| OC | 过流保护 |
| DIFF | 差动保护 |
| ZS | 零序保护 |
| TEMP | 温度保护 |
| RCD | 漏电保护器动作 |
| MAN | 人工操作分闸 |
| UNK | 未知原因跳闸 |

---

### 接口 TM-03：查询台区用户停电事件

| 属性 | 内容 |
|------|------|
| **接口编号** | TM-03 |
| **接口路径** | `GET /api/collection/outage/events` |
| **数据来源** | 用电信息采集系统主站 |
| **更新频率** | 实时（电表失电上报延迟<15分钟） |
| **用途说明** | 获取台区内停电用户列表、停电时刻、停电范围，判断全台区停电还是局部停电 |

**请求参数：**

| 参数名 | 类型 | 必选 | 说明 |
|--------|------|------|------|
| trafo_id | String | 是 | 配变编号 |
| outage_start_time | DateTime | 是 | 停电事件起始时刻 |
| time_window_minutes | Int | 否 | 时间窗口（分钟），默认30 |

**响应字段：**

```json
{
  "code": 200,
  "data": {
    "trafo_id": "XA-T001234",
    "total_user_count": 48,
    "outage_user_count": 16,
    "outage_coverage_rate": 33.3,
    "phase_distribution": {"A": 16, "B": 0, "C": 0},
    "outage_users": [
      {
        "meter_id": "XA-M0001001",
        "user_id": "XA-U10001",
        "meter_phase": "A",
        "outage_time": "2025-07-15 14:23:16",
        "restore_time": null
      }
    ]
  }
}
```

---

## 三、低压拓扑接口

### 接口 GIS-01：查询台区低压拓扑与用户归属

| 属性 | 内容 |
|------|------|
| **接口编号** | GIS-01 |
| **接口路径** | `GET /api/gis/lowvoltage/topology` |
| **数据来源** | 配网GIS系统 |
| **更新频率** | 按需更新（拓扑变更后24小时内同步） |
| **用途说明** | 获取台区低压网络拓扑，用于分析停电范围与故障位置的对应关系；雄安场景增加综合管廊归属信息 |

**请求参数：**

| 参数名 | 类型 | 必选 | 说明 |
|--------|------|------|------|
| trafo_id | String | 是 | 配变编号 |

**响应字段（雄安版新增 `pipe_gallery_id`）：**

```json
{
  "code": 200,
  "data": {
    "trafo_id": "XA-T001234",
    "cable_type": "underground_only",
    "low_voltage_branches": [
      {
        "branch_id": "XA-BR001",
        "cable_type": "underground",
        "pipe_gallery_id": "XA-PG-SECTION-A3",
        "fault_indicator_installed": true,
        "users": [
          {
            "meter_id": "XA-M0001001",
            "meter_phase": "A"
          }
        ]
      }
    ]
  }
}
```

> **雄安说明**：`cable_type` 固定为 `underground_only`（全地下），`pipe_gallery_id` 关联综合管廊段编号，用于与管廊监控数据联动。

---

## 四、上级电网状态接口

### 接口 SC-01：查询10kV馈线开关状态与保护动作

| 属性 | 内容 |
|------|------|
| **接口编号** | SC-01 |
| **接口路径** | `GET /api/scada/feeder/status` |
| **数据来源** | 配网自动化系统（SCADA） |
| **更新频率** | 实时（秒级），雄安全区覆盖 |
| **用途说明** | 查询台区所属10kV馈线的开关状态和保护动作，判断停电是否为上级馈线故障传导；雄安配网自动化全覆盖，可在30秒内自动完成此步骤 |

**请求参数：**

| 参数名 | 类型 | 必选 | 说明 |
|--------|------|------|------|
| trafo_id | String | 是 | 配变编号 |
| query_time | DateTime | 是 | 查询时刻 |
| time_window_minutes | Int | 否 | 时间窗口（分钟），默认60 |

**响应字段：**

```json
{
  "code": 200,
  "data": {
    "trafo_id": "XA-T001234",
    "feeder_id": "XA-F10KV-001",
    "feeder_breaker_status": "open",
    "feeder_breaker_last_change_time": "2025-07-15 14:23:14",
    "relay_action": {
      "action_time": "2025-07-15 14:23:14",
      "action_type": "OC",
      "reclose_result": "failed"
    },
    "same_feeder_outage_trafo_count": 15,
    "same_feeder_outage_trafo_list": ["XA-T001234", "XA-T001235"]
  }
}
```

---

## 五、停电管理接口

### 接口 OMS-01：查询计划停电工单

| 属性 | 内容 |
|------|------|
| **接口编号** | OMS-01 |
| **接口路径** | `GET /api/oms/planned-outage/query` |
| **数据来源** | 停电管理系统（OMS） |
| **更新频率** | 实时查询 |
| **用途说明** | 核查指定台区在指定时刻是否在计划停电工单范围内，是排查计划停电的直接依据，也是分析流程的第一步 |

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
    "trafo_id": "XA-T001234",
    "outage_time": "2025-07-15 14:23:15",
    "planned_outage_match": false,
    "matched_orders": []
  }
}
```

---

## 六、营销业务接口

### 接口 MKT-01：查询用户欠费与拉闸状态

| 属性 | 内容 |
|------|------|
| **接口编号** | MKT-01 |
| **接口路径** | `GET /api/marketing/user/arrear` |
| **数据来源** | 营销业务系统 |
| **更新频率** | 实时 |
| **用途说明** | 查询台区内用户欠费状态和远程拉闸记录，判断停电是否由欠费拉闸引起 |

**请求参数：**

| 参数名 | 类型 | 必选 | 说明 |
|--------|------|------|------|
| trafo_id | String | 条件必选 | 配变编号（与user_id至少填一个） |
| user_id | String | 条件必选 | 用户编号 |

**响应字段：**

```json
{
  "code": 200,
  "data": {
    "trafo_id": "XA-T001234",
    "users": [
      {
        "user_id": "XA-U10001",
        "arrear_amount": 0.0,
        "arrear_days": 0,
        "remote_cutoff_status": "closed",
        "last_remote_cutoff_time": null
      }
    ]
  }
}
```

---

## 七、气象数据接口

### 接口 WS-01：查询停电点附近气象数据

| 属性 | 内容 |
|------|------|
| **接口编号** | WS-01 |
| **接口路径** | `GET /api/weather/station/history` |
| **数据来源** | 气象数据服务 |
| **更新频率** | 历史数据按需查询，实时数据1小时更新 |
| **用途说明** | 获取停电时刻附近的气象数据；雄安场景重点关注降雨量（管廊渗水）和气温（负荷高峰），风速和覆冰数据对全地下电缆场景意义有限 |

**请求参数：**

| 参数名 | 类型 | 必选 | 说明 |
|--------|------|------|------|
| trafo_id | String | 是 | 配变编号 |
| target_time | DateTime | 是 | 目标时刻 |
| time_window_hours | Int | 否 | 时间窗口（小时），默认前后各2小时 |

**响应字段（雄安场景重点字段已标注）：**

```json
{
  "code": 200,
  "data": {
    "records": [
      {
        "record_time": "2025-07-15 14:00:00",
        "air_temperature": 38.5,
        "wind_speed": 3.2,
        "weather_condition": "雷暴",
        "rainfall_1h": 15.2,
        "rainfall_24h": 42.8,
        "humidity": 85.0,
        "solar_irradiance": 0.0
      }
    ]
  }
}
```

> **雄安使用说明**：`rainfall_24h` 是管廊渗水判断的关键字段（>50mm触发重点排查）；`solar_irradiance` 用于判断光伏逆流风险；`wind_speed` 对全地下电缆场景参考意义有限。

---

### 接口 WS-02：查询落雷记录

| 属性 | 内容 |
|------|------|
| **接口编号** | WS-02 |
| **接口路径** | `GET /api/weather/lightning/query` |
| **数据来源** | 雷电定位系统 |
| **更新频率** | 实时 |
| **用途说明** | 查询停电点附近落雷记录，辅助判断雷击感应过电压原因 |

**请求参数：**

| 参数名 | 类型 | 必选 | 说明 |
|--------|------|------|------|
| trafo_id | String | 是 | 配变编号 |
| target_time | DateTime | 是 | 停电时刻 |
| time_window_minutes | Int | 否 | 时间窗口（分钟），默认前后各30分钟 |
| radius_km | Float | 否 | 搜索半径（千米），默认2.0 |

---

## 八、设备台账接口

### 接口 PMS-01：查询设备台账信息

| 属性 | 内容 |
|------|------|
| **接口编号** | PMS-01 |
| **接口路径** | `GET /api/pms/equipment/info` |
| **数据来源** | 生产管理系统（PMS） |
| **更新频率** | 按需更新 |
| **用途说明** | 查询配变、电缆、漏电保护器等设备的投运年限、额定参数；雄安场景增加施工质量验收记录查询 |

**请求参数：**

| 参数名 | 类型 | 必选 | 说明 |
|--------|------|------|------|
| trafo_id | String | 是 | 配变编号 |
| equipment_type | String | 否 | 设备类型（transformer/cable/rcd/breaker/cable_joint） |

**响应字段（雄安版新增 `installation_quality_record`）：**

```json
{
  "code": 200,
  "data": {
    "equipments": [
      {
        "equipment_id": "XA-EQ001",
        "equipment_type": "cable_joint",
        "commission_date": "2023-03-15",
        "age_years": 2,
        "installation_quality_record": {
          "inspector": "某监理公司",
          "inspection_date": "2023-03-20",
          "result": "合格",
          "notes": ""
        }
      }
    ]
  }
}
```

---

## 九、综合管廊监控接口（雄安特有）

### 接口 PG-01：查询综合管廊环境监控数据

| 属性 | 内容 |
|------|------|
| **接口编号** | PG-01 |
| **接口路径** | `GET /api/pipe-gallery/environment/query` |
| **数据来源** | 综合管廊监控管理系统 |
| **更新频率** | 1分钟（主动上报） |
| **用途说明** | 查询指定管廊段的温湿度、积水液位和告警状态；是判断管廊渗水、高温引发电缆故障的关键数据源 |

**请求参数：**

| 参数名 | 类型 | 必选 | 说明 |
|--------|------|------|------|
| trafo_id | String | 条件必选 | 配变编号（系统自动关联所属管廊段） |
| gallery_id | String | 条件必选 | 直接指定管廊段编号（与trafo_id至少填一个） |
| start_time | DateTime | 是 | 查询起始时刻 |
| end_time | DateTime | 是 | 查询截止时刻（与start_time间隔不超过24小时） |

**响应字段：**

```json
{
  "code": 200,
  "data": {
    "gallery_id": "XA-PG-SECTION-A3",
    "linked_trafo_list": ["XA-T001234", "XA-T001235"],
    "records": [
      {
        "record_time": "2025-07-15 14:00:00",
        "temperature": 28.5,
        "humidity": 72.3,
        "water_level_cm": 0.0,
        "water_alarm": false,
        "temp_alarm": false,
        "vibration_alarm": false,
        "gas_alarm": false
      },
      {
        "record_time": "2025-07-15 14:20:00",
        "temperature": 29.1,
        "humidity": 91.2,
        "water_level_cm": 12.5,
        "water_alarm": true,
        "temp_alarm": false,
        "vibration_alarm": false,
        "gas_alarm": false
      }
    ],
    "alarm_summary": {
      "water_alarm_count": 1,
      "first_water_alarm_time": "2025-07-15 14:18:00"
    }
  }
}
```

**字段说明：**

| 字段名 | 单位 | 说明 |
|--------|------|------|
| temperature | ℃ | 管廊内环境温度，>45℃告警 |
| humidity | % | 管廊内相对湿度，>85%告警 |
| water_level_cm | cm | 积水液位（0=干燥，>5cm触发告警） |
| water_alarm | bool | 渗水告警标志（true=有渗水） |
| vibration_alarm | bool | 振动告警（施工冲击引发） |

---

### 接口 PG-02：查询综合管廊内施工工单

| 属性 | 内容 |
|------|------|
| **接口编号** | PG-02 |
| **接口路径** | `GET /api/pipe-gallery/work-order/query` |
| **数据来源** | 综合管廊管理系统 |
| **更新频率** | 按需（工单变更时同步） |
| **用途说明** | 查询指定管廊段内近期其他专业（供水/燃气/通信）的施工工单，辅助判断管廊内施工损伤电力电缆的原因 |

**请求参数：**

| 参数名 | 类型 | 必选 | 说明 |
|--------|------|------|------|
| gallery_id | String | 是 | 管廊段编号（或通过trafo_id自动关联） |
| query_days | Int | 否 | 查询最近N天的工单，默认7天 |

**响应字段：**

```json
{
  "code": 200,
  "data": {
    "gallery_id": "XA-PG-SECTION-A3",
    "work_orders": [
      {
        "order_id": "PG-WO-2025071501",
        "specialty": "供水",
        "work_type": "管件更换",
        "contractor": "某市政公司",
        "work_start_time": "2025-07-15 09:00:00",
        "work_end_time": "2025-07-15 12:00:00",
        "work_location_desc": "管廊A3段第15至18米区间",
        "proximity_to_power_cable_m": 0.8,
        "power_notification_flag": true
      }
    ]
  }
}
```

---

## 十、充电桩管理接口（雄安特有）

### 接口 EV-01：查询台区充电桩运行状态与功率

| 属性 | 内容 |
|------|------|
| **接口编号** | EV-01 |
| **接口路径** | `GET /api/evcs/charger/status` |
| **数据来源** | 充电桩管理系统（EVCS） |
| **更新频率** | 1分钟（实时上报） |
| **用途说明** | 获取台区内充电桩的总功率、分布和运行状态，用于过负荷原因分析、充电桩故障识别、谐波污染评估 |

**请求参数：**

| 参数名 | 类型 | 必选 | 说明 |
|--------|------|------|------|
| trafo_id | String | 是 | 配变编号 |
| start_time | DateTime | 是 | 查询起始时刻 |
| end_time | DateTime | 是 | 查询截止时刻 |

**响应字段：**

```json
{
  "code": 200,
  "data": {
    "trafo_id": "XA-T001234",
    "charger_count_total": 24,
    "charger_count_dc_fast": 8,
    "charger_count_ac_slow": 16,
    "summary_by_time": [
      {
        "record_time": "2025-07-15 14:00:00",
        "total_charging_power_kw": 186.5,
        "charging_charger_count": 12,
        "ev_power_ratio": 0.72
      }
    ],
    "chargers": [
      {
        "charger_id": "XA-EV-001",
        "charger_type": "DC_FAST",
        "current_power_kw": 60.0,
        "charge_status": "charging",
        "fault_code": null,
        "insulation_resistance_kohm": 1250.0
      },
      {
        "charger_id": "XA-EV-002",
        "charger_type": "DC_FAST",
        "current_power_kw": 0.0,
        "charge_status": "fault",
        "fault_code": "E012",
        "fault_desc": "绝缘检测失败",
        "insulation_resistance_kohm": 320.0
      }
    ]
  }
}
```

**字段说明：**

| 字段名 | 说明 |
|--------|------|
| ev_power_ratio | 充电桩功率占台区总负荷比例，>0.5时过负荷风险高 |
| fault_code | 充电桩故障码，非空时表示充电桩存在故障 |
| insulation_resistance_kohm | 绝缘电阻（kΩ），<500为告警，<100为严重故障 |

---

### 接口 EV-02：查询充电桩谐波数据

| 属性 | 内容 |
|------|------|
| **接口编号** | EV-02 |
| **接口路径** | `GET /api/evcs/charger/harmonic` |
| **数据来源** | 充电桩管理系统（EVCS）/ 电能质量监测仪 |
| **更新频率** | 按需查询 |
| **用途说明** | 获取台区充电桩谐波注入数据，用于分析充电桩谐波污染程度，辅助判断谐波引发漏电保护器误动的原因 |

**请求参数：**

| 参数名 | 类型 | 必选 | 说明 |
|--------|------|------|------|
| trafo_id | String | 是 | 配变编号 |
| target_time | DateTime | 是 | 目标时刻 |
| time_window_minutes | Int | 否 | 时间窗口，默认60分钟 |

**响应字段：**

```json
{
  "code": 200,
  "data": {
    "trafo_id": "XA-T001234",
    "thd_current_percent": 12.8,
    "harmonic_components": {
      "3rd": 2.1, "5th": 8.7, "7th": 5.2, "11th": 2.3
    },
    "exceed_gb14549_flag": true,
    "measurement_method": "TTU_ESTIMATION"
  }
}
```

---

## 十一、分布式能源管理接口（雄安特有）

### 接口 DG-01：查询台区分布式光伏/储能运行状态

| 属性 | 内容 |
|------|------|
| **接口编号** | DG-01 |
| **接口路径** | `GET /api/dems/distributed-energy/status` |
| **数据来源** | 分布式能源管理系统（DEMS） |
| **更新频率** | 5分钟 |
| **用途说明** | 获取台区内光伏系统出力、是否存在逆流、储能系统充放电状态和模式切换记录，用于判断光伏逆流引发误动和储能冲击引发跳闸的原因 |

**请求参数：**

| 参数名 | 类型 | 必选 | 说明 |
|--------|------|------|------|
| trafo_id | String | 是 | 配变编号 |
| start_time | DateTime | 是 | 查询起始时刻 |
| end_time | DateTime | 是 | 查询截止时刻 |

**响应字段：**

```json
{
  "code": 200,
  "data": {
    "trafo_id": "XA-T001234",
    "pv_system_count": 3,
    "ess_system_count": 1,
    "time_series": [
      {
        "record_time": "2025-07-15 12:00:00",
        "pv_total_output_kw": 85.2,
        "trafo_total_load_kw": 42.5,
        "net_power_kw": -42.7,
        "reverse_power_flag": true,
        "solar_irradiance": 950.0,
        "ess_status": "discharging",
        "ess_output_kw": 15.0
      }
    ],
    "ess_mode_change_log": [
      {
        "change_time": "2025-07-15 11:58:33",
        "from_status": "charging",
        "to_status": "discharging",
        "trigger_reason": "peak_shaving_schedule"
      }
    ]
  }
}
```

**字段说明：**

| 字段名 | 说明 |
|--------|------|
| net_power_kw | 台区净功率（pv_output - trafo_load），负值表示逆流 |
| reverse_power_flag | true=存在光伏逆流，可能触发保护误动 |
| ess_mode_change_log | 储能模式切换记录，切换时刻与停电时刻对比分析 |

---

## 十二、建设工程施工管理接口（雄安特有）

### 接口 CONST-01：查询台区附近活跃施工工单

| 属性 | 内容 |
|------|------|
| **接口编号** | CONST-01 |
| **接口路径** | `GET /api/construction/work-order/nearby` |
| **数据来源** | 雄安新区建设工程施工管理系统 |
| **更新频率** | 按需（工单变更时同步）；停电触发时自动查询 |
| **用途说明** | 查询台区附近指定范围内的活跃施工工单，结合GIS进行施工位置与电缆路径的空间叠加分析，是判断施工外力破坏的首要接口；雄安建设期该接口为P0优先级 |

**请求参数：**

| 参数名 | 类型 | 必选 | 说明 |
|--------|------|------|------|
| trafo_id | String | 是 | 配变编号 |
| radius_m | Int | 否 | 搜索半径（米），默认500 |
| outage_time | DateTime | 是 | 停电时刻（查询此时刻处于活跃状态的工单） |

**响应字段：**

```json
{
  "code": 200,
  "data": {
    "trafo_id": "XA-T001234",
    "search_radius_m": 500,
    "active_work_order_count": 2,
    "work_orders": [
      {
        "order_id": "XA-WO-2025071501",
        "project_name": "某路基础施工项目",
        "work_type": "基础施工",
        "construction_method": "顶管",
        "contractor": "某建工集团",
        "actual_start_time": "2025-07-12 08:00:00",
        "planned_end_time": "2025-07-25 18:00:00",
        "work_location_wkt": "POLYGON((116.08 38.99, 116.09 38.99, ...))",
        "distance_to_trafo_m": 180.0,
        "power_protection_permit": false,
        "gis_overlap_analysis": {
          "overlaps_with_lv_cable": true,
          "overlapping_cable_segment_id": "XA-CABLE-BR001-SEG3",
          "overlap_length_m": 45.0
        }
      }
    ]
  }
}
```

**字段说明：**

| 字段名 | 说明 |
|--------|------|
| power_protection_permit | false表示施工未取得电力设施保护许可，外力破坏风险更高 |
| gis_overlap_analysis.overlaps_with_lv_cable | true表示施工范围与低压电缆路径存在空间交叉，是外力破坏的重要证据 |
| overlap_length_m | 施工范围与电缆路径的空间交叉长度，越长风险越高 |

---

## 十三、接口调用顺序（雄安场景优化版）

```
停电事件触发（用电信息采集系统自动感知）
    ↓
【自动完成，<30秒】
Step 1  OMS-01  → 查询计划停电工单（有工单则直接确认原因，结束分析）
Step 1  SC-01   → 查询10kV馈线/变电站状态（上级停电直接确认）
    ↓
【雄安优先，<2分钟】
Step 2  CONST-01 → 查询台区附近活跃施工工单 + GIS叠加分析（建设期高频原因）
Step 2  PG-01    → 查询综合管廊环境监控数据（管廊渗水/温度告警）
Step 2  PG-02    → 查询管廊内其他专业施工工单
    ↓
【设备分析，<5分钟】
Step 3  TM-01   → 查询配变停电前电气量（温度/负载率/零序电流/功率方向）
Step 3  TM-02   → 查询配变保护动作类型和重合闸结果
Step 3  TM-03   → 查询台区停电用户范围（全台区/局部/单相）
    ↓
【负荷与新能源分析，<5分钟】
Step 4  EV-01   → 查询充电桩总功率和故障码（过负荷/充电桩故障）
Step 4  EV-02   → 查询充电桩谐波数据（谐波误动）
Step 4  DG-01   → 查询光伏出力/逆流/储能模式切换（新能源相关误动）
Step 4  WS-01   → 查询气象数据（气温影响负荷，降雨量影响管廊）
Step 4  WS-02   → 查询落雷记录（雷击感应过电压）
    ↓
【用户侧和台账，<3分钟】
Step 5  MKT-01  → 查询欠费拉闸状态（单用户停电重点排查）
Step 5  PMS-01  → 查询设备台账和施工质量记录
Step 5  PMS-02  → 查询运维巡视和缺陷记录
    ↓
【现场确认（抢修完成后）】
Step 6  RP-01   → 查询故障抢修工单现场结论
```

---

## 十四、接口超时与容错建议

| 接口类别 | 建议超时设置 | 失败处理策略 | 雄安说明 |
|---------|------------|------------|---------|
| 实时类（SCADA/TTU）| 5秒 | 重试1次，失败标记缺失 | 雄安全覆盖，超时概率极低 |
| 综合管廊监控 | 5秒 | 重试1次，失败降低置信度并说明数据缺失 | P0优先级，需保障 |
| 充电桩管理 | 10秒 | 重试1次，失败则略过充电桩分析维度 | 多运营商接口，稳定性参差 |
| 分布式能源管理 | 10秒 | 重试1次，失败则略过新能源分析维度 | 多系统集成，需容错 |
| 建设工程施工管理 | 15秒 | 重试2次，失败时在结论中注明无法核查施工信息 | 跨部门系统，延迟可能较大 |
| 历史查询类（OMS/MKT/PMS）| 15秒 | 重试1次，失败标记缺失 | — |
| 气象/雷电类（外部） | 10秒 | 重试2次，失败跳过，注明数据不完整 | — |
