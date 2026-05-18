# 400V低压配电网停电原因分析——数据需求清单

> 本清单基于《400V低压配电网停电原因判断逻辑清单》抽象生成，目标是将业务判断规则转化为**可获取的数据字段与计算特征**，为智能体设计、提示词工程和数据接口对接提供基础。

---

## 一、文档说明

数据需求拆解为五层：

```
停电原因类别 → 判断规则 → 所需特征（Feature） → 原始字段（Field） → 数据来源系统（Source）
```

---

## 二、总体结构设计

| 数据域 | 主要覆盖原因类别 | 核心价值 |
|--------|----------------|---------|
| 配变监测数据 | 设备类（1类）、负荷类（2类） | 配变运行状态实时感知 |
| 低压采集数据 | 设备类（1类）、用户侧（4类） | 用户停电范围和时刻定位 |
| 配网自动化数据 | 上级传导（5类）、设备类（1类） | 上级电网状态判断 |
| 营销业务数据 | 用户侧（4类） | 欠费、用户信息核查 |
| 停电管理数据 | 上级传导（5类） | 计划停电核查 |
| 气象环境数据 | 外部环境类（3类） | 自然灾害诱因判断 |
| 设备台账数据 | 设备类（1类）、外部环境（3类） | 设备年限、运维记录查询 |
| 故障抢修数据 | 外部环境类（3类）、设备类（1类） | 现场勘查结果确认 |

---

## 三、核心特征字典（Feature Dictionary）

### 3.1 配变运行特征

| 特征名 | 含义 | 原始字段 | 数据来源系统 |
|--------|------|---------|------------|
| trafo_oil_temp | 配变油温（℃） | oil_temperature | 配变监测终端（TTU） |
| trafo_winding_temp | 配变绕组温度（℃） | winding_temperature | 配变监测终端（TTU） |
| trafo_load_rate | 配变负载率（%） | active_power / rated_capacity | TTU + 设备台账 |
| trafo_3phase_current | 配变三相电流（A/B/C相） | ia, ib, ic | 配变监测终端（TTU） |
| trafo_3phase_voltage | 配变低压侧三相电压（V） | ua, ub, uc | 配变监测终端（TTU） |
| trafo_zero_seq_current | 配变零序电流（A） | i0 | 配变监测终端（TTU） |
| trafo_3phase_imbalance | 三相不平衡度（%） | (Imax - Iavg) / Iavg × 100 | 计算字段（基于ia/ib/ic） |
| trafo_relay_action_type | 保护动作类型 | relay_action_code | 配网自动化系统 |
| trafo_reclose_success | 重合闸是否成功 | reclose_result | 配网自动化系统 |
| trafo_harmonic_thd | 电流谐波畸变率THD-I（%） | thd_current | 电能质量监测仪/TTU |

---

### 3.2 低压采集与停电感知特征

| 特征名 | 含义 | 原始字段 | 数据来源系统 |
|--------|------|---------|------------|
| meter_outage_time | 用户电表失电时刻 | outage_event_time | 用电信息采集系统 |
| meter_restore_time | 用户电表复电时刻 | restore_event_time | 用电信息采集系统 |
| outage_user_count | 台区停电用户数量 | count(meter_id) WHERE outage=1 | 用电信息采集系统 |
| outage_user_coverage_rate | 停电用户占台区总用户比例（%） | outage_cnt / total_cnt × 100 | 计算字段 |
| outage_phase_distribution | 停电用户的相别分布（A/B/C） | meter_phase_label | 用电信息采集系统 + 配网GIS |
| meter_current_before_outage | 停电前15分钟内电流 | current_value | 用电信息采集系统 |
| meter_voltage_trend | 停电前24小时内用户电压趋势 | voltage_value, time_stamp | 用电信息采集系统 |
| meter_leakage_current | 漏电电流值（mA） | leakage_current | 智能电表（若支持） |
| fault_indicator_status | 低压分支故障指示器翻牌状态 | indicator_status, line_segment_id | 配网自动化系统 |

---

### 3.3 上级电网状态特征

| 特征名 | 含义 | 原始字段 | 数据来源系统 |
|--------|------|---------|------------|
| hv_feeder_breaker_status | 10kV馈线出口断路器状态 | breaker_status | 配网自动化系统（SCADA） |
| hv_feeder_relay_action_time | 10kV馈线保护动作时刻 | relay_action_time | 配网自动化系统 |
| same_feeder_outage_area_cnt | 同馈线同时停电台区数量 | count(substation_id) | 停电管理系统（OMS） |
| hv_voltage_at_trafo_primary | 配变高压侧电压是否正常 | primary_voltage | 配变监测终端（TTU） |
| planned_outage_flag | 是否为计划停电时间段内 | outage_plan_id | 停电管理系统（OMS） |
| planned_outage_scope | 计划停电涉及台区/馈线范围 | affected_transformer_list | 停电管理系统（OMS） |
| dispatch_order_flag | 是否有调度拉闸指令 | dispatch_instruction_id | 调度自动化系统 |

---

### 3.4 气象与外部环境特征

| 特征名 | 含义 | 原始字段 | 数据来源系统 |
|--------|------|---------|------------|
| weather_type_at_outage | 停电时刻天气类型（晴/雨/雷暴/大风/冰雪） | weather_condition | 气象数据服务 |
| wind_speed_at_outage | 停电时刻风速（m/s） | wind_speed | 气象数据服务 |
| lightning_nearby_flag | 停电点附近2km内是否有落雷 | lightning_location, distance | 雷电定位系统 |
| temperature_at_outage | 停电时刻气温（℃） | air_temperature | 气象数据服务 |
| rainfall_24h | 停电前24小时累计降雨量（mm） | rainfall_24h | 气象数据服务 |
| construction_nearby_flag | 台区周边是否有施工工单 | work_order_location, distance | 施工申报系统/GIS |
| infrared_hotspot_temp | 设备红外测温热点温度（℃） | hotspot_temp, delta_temp | 运维红外测温记录 |

---

### 3.5 营销与用户特征

| 特征名 | 含义 | 原始字段 | 数据来源系统 |
|--------|------|---------|------------|
| user_arrear_amount | 用户欠费金额（元） | arrear_amount | 营销业务系统 |
| user_arrear_days | 用户欠费天数（天） | arrear_days | 营销业务系统 |
| remote_cutoff_flag | 是否已执行远程拉闸 | remote_switch_result | 用电信息采集系统 |
| remote_cutoff_time | 远程拉闸执行时刻 | switch_operation_time | 用电信息采集系统 |
| user_complaint_record | 用户投诉记录（停电时间窗口内） | complaint_time, content | 营销业务系统/95598 |
| user_power_application | 用户主动申请停电记录 | user_outage_application_id | 营销业务系统 |

---

### 3.6 设备台账特征

| 特征名 | 含义 | 原始字段 | 数据来源系统 |
|--------|------|---------|------------|
| trafo_age_years | 配变运行年限（年） | commission_date | 设备台账（PMS） |
| trafo_last_test_date | 配变上次预防性试验日期 | last_test_date | 设备台账（PMS） |
| cable_age_years | 低压电缆运行年限（年） | cable_commission_date | 设备台账（PMS）/GIS |
| rcd_age_years | 漏电保护器运行年限（年） | rcd_install_date | 设备台账（PMS） |
| rcd_trip_count_6m | 漏电保护器近6个月动作次数 | trip_event_log | 运维记录 |
| tap_changer_last_adjust | 分接头最近调整日期 | tap_adjust_date | 设备台账（PMS） |
| maintenance_record | 近期运维巡视记录 | inspection_result | 生产管理系统（PMS） |

---

## 四、规则与特征映射关系

### 4.1 配变故障（1.1类）

| 判断规则 | 所需特征 |
|---------|---------|
| 停电前温度持续上升且超阈值 | trafo_oil_temp, trafo_winding_temp |
| 台区全部用户同时停电 | outage_user_coverage_rate |
| 重合闸不成功 | trafo_reclose_success |
| 保护动作类型为差动/零序保护 | trafo_relay_action_type |
| 配变运行年限长，预试间隔长 | trafo_age_years, trafo_last_test_date |

---

### 4.2 低压线路故障（1.2类）

| 判断规则 | 所需特征 |
|---------|---------|
| 停电范围为局部分支（非全台区） | outage_user_coverage_rate, outage_phase_distribution |
| 低压故障指示器翻牌 | fault_indicator_status |
| 相应相电流异常（骤降或增大） | trafo_3phase_current |
| 合闸后立即再次跳闸 | trafo_reclose_success |
| 电缆运行年限长 | cable_age_years |

---

### 4.3 过负荷保护动作（2.1类）

| 判断规则 | 所需特征 |
|---------|---------|
| 停电前负载率超100% | trafo_load_rate |
| 保护动作类型为过流 | trafo_relay_action_type |
| 降负荷后复位成功 | trafo_reclose_success |
| 三相不平衡度高（单相过负荷） | trafo_3phase_imbalance |
| 停电时刻处于高温天气高峰 | temperature_at_outage |

---

### 4.4 外部环境原因（3类）

| 判断规则 | 所需特征 |
|---------|---------|
| 停电时刻有雷暴且附近有落雷 | weather_type_at_outage, lightning_nearby_flag |
| 停电时刻风速超过安全阈值 | wind_speed_at_outage |
| 台区附近有施工工单 | construction_nearby_flag |
| 设备红外测温发现热点 | infrared_hotspot_temp |
| 停电前有强降雨（绝缘受潮） | rainfall_24h |

---

### 4.5 用户侧原因（4类）

| 判断规则 | 所需特征 |
|---------|---------|
| 用户欠费超阈值 | user_arrear_amount, user_arrear_days |
| 远程拉闸指令执行成功 | remote_cutoff_flag, remote_cutoff_time |
| 停电时刻与拉闸指令时刻吻合 | remote_cutoff_time, meter_outage_time |
| 停电范围仅限该单一用户 | outage_user_count |
| 用户投诉/报修描述指向用户侧 | user_complaint_record |

---

### 4.6 上级传导原因（5类）

| 判断规则 | 所需特征 |
|---------|---------|
| 同馈线多台区同时停电 | same_feeder_outage_area_cnt |
| 10kV馈线开关保护跳闸 | hv_feeder_breaker_status, hv_feeder_relay_action_time |
| 配变高压侧电压突然消失 | hv_voltage_at_trafo_primary |
| 有对应计划停电工单 | planned_outage_flag, planned_outage_scope |
| 有调度拉闸指令 | dispatch_order_flag |

---

## 五、原始数据表设计

### 5.1 配变实时监测表（trafo_realtime）

| 字段名 | 类型 | 说明 |
|--------|------|------|
| trafo_id | VARCHAR | 配变编号 |
| record_time | DATETIME | 数据时刻（5分钟间隔） |
| ia / ib / ic | FLOAT | 三相电流（A） |
| ua / ub / uc | FLOAT | 三相电压（V） |
| i0 | FLOAT | 零序电流（A） |
| active_power | FLOAT | 有功功率（kW） |
| oil_temperature | FLOAT | 油温（℃） |
| load_rate | FLOAT | 负载率（%） |
| thd_current | FLOAT | 电流谐波畸变率（%） |

---

### 5.2 用户停电事件表（meter_outage_event）

| 字段名 | 类型 | 说明 |
|--------|------|------|
| event_id | VARCHAR | 停电事件ID |
| meter_id | VARCHAR | 电表编号 |
| user_id | VARCHAR | 用户编号 |
| trafo_id | VARCHAR | 所属台区配变编号 |
| meter_phase | CHAR | 所属相别（A/B/C） |
| outage_time | DATETIME | 失电时刻 |
| restore_time | DATETIME | 复电时刻 |
| outage_duration_min | INT | 停电时长（分钟） |

---

### 5.3 配网运行状态表（grid_operation_status）

| 字段名 | 类型 | 说明 |
|--------|------|------|
| feeder_id | VARCHAR | 10kV馈线编号 |
| breaker_id | VARCHAR | 开关编号 |
| status_time | DATETIME | 状态时刻 |
| breaker_status | TINYINT | 开关状态（0=断开，1=合闸） |
| relay_action_code | VARCHAR | 保护动作类型编码 |
| reclose_result | TINYINT | 重合闸结果（0=失败，1=成功，NULL=未重合） |
| primary_voltage | FLOAT | 高压侧电压（kV） |

---

### 5.4 计划停电工单表（planned_outage_order）

| 字段名 | 类型 | 说明 |
|--------|------|------|
| order_id | VARCHAR | 停电工单编号 |
| plan_start_time | DATETIME | 计划停电开始时间 |
| plan_end_time | DATETIME | 计划复电时间 |
| actual_start_time | DATETIME | 实际停电时间 |
| actual_end_time | DATETIME | 实际复电时间 |
| affected_feeder_list | TEXT | 涉及10kV馈线列表（JSON） |
| affected_trafo_list | TEXT | 涉及台区列表（JSON） |
| outage_reason | VARCHAR | 停电原因说明 |
| notify_flag | TINYINT | 是否已通知用户（0=否，1=是） |

---

### 5.5 营销业务用户表（marketing_user）

| 字段名 | 类型 | 说明 |
|--------|------|------|
| user_id | VARCHAR | 用户编号 |
| meter_id | VARCHAR | 关联电表编号 |
| user_name | VARCHAR | 用户名称 |
| trafo_id | VARCHAR | 所属台区 |
| arrear_amount | FLOAT | 当前欠费金额（元） |
| arrear_days | INT | 欠费天数 |
| remote_cutoff_status | TINYINT | 远程拉闸状态（0=合闸，1=已拉闸） |
| contract_capacity | FLOAT | 合同容量（kVA） |

---

### 5.6 气象数据表（weather_data）

| 字段名 | 类型 | 说明 |
|--------|------|------|
| station_id | VARCHAR | 气象站编号 |
| record_time | DATETIME | 数据时刻（1小时间隔） |
| air_temperature | FLOAT | 气温（℃） |
| wind_speed | FLOAT | 风速（m/s） |
| weather_condition | VARCHAR | 天气类型（晴/阴/雨/雷暴/大风/冰雪） |
| rainfall_1h | FLOAT | 1小时降雨量（mm） |
| rainfall_24h | FLOAT | 24小时累计降雨量（mm） |
| humidity | FLOAT | 相对湿度（%） |

---

### 5.7 设备台账表（equipment_registry）

| 字段名 | 类型 | 说明 |
|--------|------|------|
| equipment_id | VARCHAR | 设备编号 |
| equipment_type | VARCHAR | 设备类型（配变/电缆/开关/漏保等） |
| trafo_id | VARCHAR | 所属台区（若适用） |
| rated_capacity | FLOAT | 额定容量（kVA，配变适用） |
| commission_date | DATE | 投运日期 |
| last_test_date | DATE | 上次预防性试验日期 |
| last_inspection_date | DATE | 上次运维巡视日期 |
| maintenance_notes | TEXT | 巡视发现的缺陷记录 |

---

### 5.8 故障抢修工单表（repair_work_order）

| 字段名 | 类型 | 说明 |
|--------|------|------|
| order_id | VARCHAR | 工单编号 |
| trafo_id | VARCHAR | 涉及台区 |
| report_time | DATETIME | 报修时间 |
| dispatch_time | DATETIME | 派工时间 |
| arrive_time | DATETIME | 到达现场时间 |
| restore_time | DATETIME | 复电时间 |
| fault_type | VARCHAR | 故障类型（现场判断） |
| fault_location | VARCHAR | 故障位置描述 |
| fault_cause | VARCHAR | 故障原因（现场填写） |
| field_notes | TEXT | 现场处理情况记录 |

---

## 六、数据优先级与获取难度评估

| 数据项 | 停电原因分析必要性 | 优先级 | 获取难度 | 说明 |
|--------|----------------|--------|---------|------|
| 配变监测数据（TTU） | 高 | P0 | 中（需TTU已安装且在线） | 核心电气量，判断设备类和负荷类必须 |
| 用户停电事件（采集系统） | 高 | P0 | 低（系统已有） | 确定停电范围和时刻 |
| 10kV馈线状态（SCADA） | 高 | P0 | 低（系统已有） | 排查上级传导原因必须 |
| 计划停电工单（OMS） | 高 | P0 | 低（系统已有） | 排查计划停电必须 |
| 气象数据 | 中 | P1 | 低（外部接入） | 外部环境原因判断 |
| 营销欠费数据 | 中 | P1 | 低（系统已有） | 欠费停电判断 |
| 设备台账（PMS） | 中 | P1 | 中（数据质量参差） | 设备年限、运维记录 |
| 低压故障指示器数据 | 中 | P1 | 高（需现场安装改造） | 低压线路故障定位 |
| 电能质量监测数据 | 低 | P2 | 高（专用仪器） | 谐波分析，非大规模覆盖 |
| 雷电定位数据 | 低 | P2 | 低（第三方接入） | 雷击原因辅助判断 |
| 红外测温数据 | 低 | P2 | 高（需定期巡视） | 接头腐蚀缺陷辅助 |
