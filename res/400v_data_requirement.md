# 400V低压配电网停电原因分析——数据需求清单（雄安新区场景）

> **场景说明**：本清单面向雄安新区400V低压配电网，基于《停电原因判断逻辑清单（雄安新区版）》抽象生成。雄安新区的特殊性体现在：新增综合管廊监控、充电桩管理、分布式能源、建设工程施工管理四类雄安特有数据域；删除架空线/电杆相关的风速、覆冰等气象特征；TTU全覆盖使得核心电气量数据完整，整体数据获取条件优于通用台区场景。

---

## 一、数据域总体结构

| 数据域 | 主要覆盖停电原因 | 核心价值 | 雄安可用性 |
|--------|----------------|---------|-----------|
| 配变监测数据（TTU） | 设备类（1类）、负荷类（2类） | 配变运行状态实时感知 | ✅ 全覆盖，高优先级 |
| 低压采集数据 | 设备类（1类）、用户侧（4类） | 用户停电范围和时刻定位 | ✅ 全覆盖 |
| 配网自动化数据 | 上级传导（5类）、设备类（1类） | 上级电网状态判断 | ✅ 全覆盖，秒级响应 |
| 营销业务数据 | 用户侧（4类） | 欠费、用户信息核查 | ✅ 已有 |
| 停电管理数据 | 上级传导（5类） | 计划停电核查 | ✅ 已有 |
| 气象环境数据 | 外部环境类（3类） | 强降雨、雷电判断 | ✅ 外部接入（注：风速/覆冰数据对雄安意义有限） |
| 设备台账数据 | 设备类（1类） | 设备年限、运维记录 | ✅ 已有 |
| 故障抢修数据 | 全类型（现场确认） | 现场勘查结果确认 | ✅ 已有 |
| **综合管廊监控数据** | 设备类1.2.3、外部环境3类 | 管廊温湿度、渗水、施工告警 | ⚡ 雄安特有，关键数据域 |
| **充电桩管理数据** | 负荷类2类、用户侧4.1.1 | 充电功率、谐波、故障码 | ⚡ 雄安特有，高密度部署 |
| **分布式能源管理数据** | 负荷类2.4类 | 光伏出力、逆流、储能状态 | ⚡ 雄安特有，建筑光伏普遍 |
| **建设工程施工管理数据** | 外部环境3.2类 | 施工工单、施工位置、作业类型 | ⚡ 雄安特有，建设期高频原因 |

---

## 二、特征字典（Feature Dictionary）

### 2.1 配变运行特征

| 特征名 | 含义 | 原始字段 | 数据来源系统 |
|--------|------|---------|------------|
| trafo_oil_temp | 配变油温（℃） | oil_temperature | 配变监测终端（TTU） |
| trafo_winding_temp | 配变绕组温度（℃，干式配变） | winding_temperature | 配变监测终端（TTU） |
| trafo_load_rate | 配变负载率（%） | active_power / rated_capacity | TTU + 设备台账 |
| trafo_3phase_current | 配变三相电流（A/B/C相） | ia, ib, ic | 配变监测终端（TTU） |
| trafo_3phase_voltage | 配变低压侧三相电压（V） | ua, ub, uc | 配变监测终端（TTU） |
| trafo_zero_seq_current | 配变零序电流（A） | i0 | 配变监测终端（TTU） |
| trafo_3phase_imbalance | 三相不平衡度（%） | (Imax - Iavg) / Iavg × 100 | 计算字段 |
| trafo_relay_action_type | 保护动作类型编码 | relay_action_code | 配网自动化系统 |
| trafo_reclose_success | 重合闸是否成功 | reclose_result | 配网自动化系统 |
| trafo_harmonic_thd | 电流总谐波畸变率THD-I（%） | thd_current | TTU / 电能质量监测仪 |
| trafo_power_direction | 低压侧有功功率方向（正向/逆流） | active_power_sign | TTU（光伏逆流判断关键） |

---

### 2.2 低压采集与停电感知特征

| 特征名 | 含义 | 原始字段 | 数据来源系统 |
|--------|------|---------|------------|
| meter_outage_time | 用户电表失电时刻 | outage_event_time | 用电信息采集系统 |
| meter_restore_time | 用户电表复电时刻 | restore_event_time | 用电信息采集系统 |
| outage_user_count | 台区停电用户数量 | count(meter_id) WHERE outage=1 | 用电信息采集系统 |
| outage_user_coverage_rate | 停电用户占台区总用户比例（%） | outage_cnt / total_cnt × 100 | 计算字段 |
| outage_phase_distribution | 停电用户的相别分布（A/B/C） | meter_phase_label | 用电信息采集系统 + GIS |
| meter_current_before_outage | 停电前15分钟内电流 | current_value | 用电信息采集系统 |
| meter_voltage_trend | 停电前24小时内用户电压趋势 | voltage_value, time_stamp | 用电信息采集系统 |
| fault_indicator_status | 低压分支故障指示器翻牌状态 | indicator_status, line_segment_id | 配网自动化系统 |

---

### 2.3 上级电网状态特征

| 特征名 | 含义 | 原始字段 | 数据来源系统 |
|--------|------|---------|------------|
| hv_feeder_breaker_status | 10kV馈线出口断路器状态 | breaker_status | 配网自动化系统（SCADA） |
| hv_feeder_relay_action_time | 10kV馈线保护动作时刻 | relay_action_time | 配网自动化系统 |
| same_feeder_outage_area_cnt | 同馈线同时停电台区数量 | count(trafo_id) WHERE outage=1 | 停电管理系统（OMS） |
| hv_voltage_at_trafo_primary | 配变高压侧电压是否正常 | primary_voltage | 配变监测终端（TTU） |
| planned_outage_flag | 是否为计划停电时间段内 | outage_plan_id | 停电管理系统（OMS） |
| planned_outage_scope | 计划停电涉及台区/馈线范围 | affected_transformer_list | 停电管理系统（OMS） |
| dispatch_order_flag | 是否有调度拉闸指令 | dispatch_instruction_id | 调度自动化系统 |

---

### 2.4 气象与雷电特征（雄安场景适用范围说明）

> **注意**：雄安新区低压电网全地下电缆化，风速、覆冰等影响架空线的气象特征**不适用**，已从本清单删除。保留降雨量（管廊渗水判断）和雷电数据（感应雷击判断）。

| 特征名 | 含义 | 原始字段 | 数据来源系统 | 雄安适用性 |
|--------|------|---------|------------|----------|
| weather_type_at_outage | 停电时刻天气类型 | weather_condition | 气象数据服务 | ✅ 适用 |
| temperature_at_outage | 停电时刻气温（℃） | air_temperature | 气象数据服务 | ✅ 适用（影响负荷特性） |
| rainfall_24h | 停电前24小时累计降雨量（mm） | rainfall_24h | 气象数据服务 | ✅ 关键（管廊渗水判断） |
| lightning_nearby_flag | 停电点附近2km内是否有落雷 | lightning_location, distance | 雷电定位系统 | ✅ 适用（感应雷击） |
| ~~wind_speed_at_outage~~ | ~~风速~~ | ~~wind_speed~~ | — | ❌ 雄安无架空线，不适用 |

---

### 2.5 营销与用户特征

| 特征名 | 含义 | 原始字段 | 数据来源系统 |
|--------|------|---------|------------|
| user_arrear_amount | 用户欠费金额（元） | arrear_amount | 营销业务系统 |
| user_arrear_days | 用户欠费天数（天） | arrear_days | 营销业务系统 |
| remote_cutoff_flag | 是否已执行远程拉闸 | remote_switch_result | 用电信息采集系统 |
| remote_cutoff_time | 远程拉闸执行时刻 | switch_operation_time | 用电信息采集系统 |
| user_complaint_record | 用户投诉记录 | complaint_time, content | 营销业务系统/95598 |
| user_contract_capacity | 用户合同容量（kVA） | contract_capacity | 营销业务系统 |

---

### 2.6 设备台账特征

| 特征名 | 含义 | 原始字段 | 数据来源系统 |
|--------|------|---------|------------|
| trafo_age_years | 配变运行年限（年） | commission_date | 设备台账（PMS） |
| trafo_last_test_date | 配变上次预防性试验日期 | last_test_date | 设备台账（PMS） |
| cable_age_years | 低压电缆运行年限（年） | cable_commission_date | 设备台账/GIS |
| cable_capacity_utilization | 电缆长期负载率（历史均值，%） | historical_current / rated_current | 计算字段 |
| rcd_age_years | 漏电保护器运行年限（年） | rcd_install_date | 设备台账（PMS） |
| cable_installation_quality | 电缆/接头施工质量验收结论 | quality_inspection_result | 施工验收记录（PMS） |
| infrared_hotspot_temp | 电缆接头红外测温热点温度（℃） | hotspot_temp, delta_temp | 管廊定期巡检记录 |

---

### 2.7 综合管廊监控特征（雄安特有）

| 特征名 | 含义 | 原始字段 | 数据来源系统 |
|--------|------|---------|------------|
| gallery_temperature | 管廊内温度（℃） | gallery_temp | 综合管廊监控系统 |
| gallery_humidity | 管廊内相对湿度（%） | gallery_humidity | 综合管廊监控系统 |
| gallery_water_level | 管廊内积水液位（cm） | water_level | 综合管廊监控系统 |
| gallery_water_alarm_flag | 管廊渗水告警标志 | water_alarm | 综合管廊监控系统 |
| gallery_temp_alarm_flag | 管廊温度超限告警标志 | temp_alarm | 综合管廊监控系统 |
| gallery_vibration_alarm_flag | 管廊内振动告警（施工损伤感知） | vibration_alarm | 综合管廊监控系统 |
| gallery_other_work_order_flag | 管廊内近7天是否有其他专业施工 | work_order_exists | 综合管廊管理系统 |

---

### 2.8 充电桩管理特征（雄安特有）

| 特征名 | 含义 | 原始字段 | 数据来源系统 |
|--------|------|---------|------------|
| ev_charger_total_power | 台区充电桩总功率（kW） | sum(charger_power) | 充电桩管理系统 |
| ev_charger_power_ratio | 充电桩功率占台区总负荷比例（%） | ev_power / total_power × 100 | 计算字段 |
| ev_charger_fault_code | 充电桩故障码 | fault_code | 充电桩管理系统 |
| ev_charger_insulation_alarm | 充电桩绝缘检测告警 | insulation_fault | 充电桩管理系统 |
| ev_dc_fast_charger_count | 台区直流快充桩数量（谐波源） | dc_charger_count | 充电桩管理系统 |
| ev_charger_harmonic_level | 充电桩谐波注入水平（5次分量，A） | harmonic_5th_current | 充电桩管理系统（若支持） |

---

### 2.9 分布式能源特征（雄安特有）

| 特征名 | 含义 | 原始字段 | 数据来源系统 |
|--------|------|---------|------------|
| pv_total_output | 台区光伏总出力（kW） | sum(pv_power) | 分布式能源管理系统（DEMS） |
| pv_reverse_power_flag | 是否存在光伏逆流（台区净功率为负） | net_power < 0 | 计算字段（基于TTU有功方向） |
| pv_irradiance | 停电时刻太阳辐照度（W/m²） | solar_irradiance | 气象数据/DEMS |
| pv_inverter_fault_code | 光伏逆变器故障码 | inverter_fault_code | 分布式能源管理系统 |
| ess_charge_discharge_status | 储能系统充放电状态 | ess_status | 储能管理系统（BMS/EMS） |
| ess_mode_switch_time | 储能系统模式切换时刻 | mode_change_time | 储能管理系统（BMS/EMS） |
| ess_pcs_output_current | 储能PCS输出电流（A） | pcs_current | 储能管理系统 |

---

### 2.10 建设工程施工特征（雄安特有）

| 特征名 | 含义 | 原始字段 | 数据来源系统 |
|--------|------|---------|------------|
| construction_nearby_flag | 台区500m范围内是否有活跃施工工单 | work_order_active | 建设工程施工管理系统 |
| construction_type | 施工类型（基础/顶管/明挖/管廊内施工等） | construction_type | 建设工程施工管理系统 |
| construction_location_overlap | 施工位置与低压电缆路径是否在GIS上交叉 | spatial_overlap_flag | GIS空间叠加计算 |
| construction_permit_status | 施工是否取得电力设施保护许可 | power_protection_permit | 建设工程施工管理系统 |
| construction_start_time | 施工开始时间 | work_start_time | 建设工程施工管理系统 |

---

## 三、规则与特征映射关系

### 3.1 配变故障（1.1类）

| 判断规则 | 所需特征 |
|---------|---------|
| 停电前温度持续上升且超阈值 | trafo_oil_temp, trafo_winding_temp |
| 台区全部用户同时停电 | outage_user_coverage_rate |
| 重合闸不成功 | trafo_reclose_success |
| 保护动作类型为差动/零序保护 | trafo_relay_action_type |
| 配变运行年限及预试记录 | trafo_age_years, trafo_last_test_date |

---

### 3.2 低压电缆故障（1.2类，雄安全地下电缆）

| 判断规则 | 所需特征 |
|---------|---------|
| 停电范围为局部分支（非全台区） | outage_user_coverage_rate |
| 相应相零序电流突变 | trafo_zero_seq_current |
| 合闸后立即再次跳闸 | trafo_reclose_success |
| 管廊内温度/湿度/渗水告警 | gallery_temperature, gallery_water_alarm_flag |
| 电缆施工质量验收记录 | cable_installation_quality |
| 管廊内其他专业施工工单 | gallery_other_work_order_flag |
| 电缆接头红外测温热点 | infrared_hotspot_temp |

---

### 3.3 过负荷保护动作（2.1类）

| 判断规则 | 所需特征 |
|---------|---------|
| 停电前负载率超100% | trafo_load_rate |
| 保护动作类型为过流 | trafo_relay_action_type |
| 充电桩功率高峰（雄安关键因素） | ev_charger_total_power, ev_charger_power_ratio |
| 三相不平衡度高（单相过负荷） | trafo_3phase_imbalance |
| 停电时刻处于高温/高负荷时段 | temperature_at_outage |

---

### 3.4 充电桩/新能源引发故障（2.2/2.4类，雄安特有）

| 判断规则 | 所需特征 |
|---------|---------|
| 谐波畸变率超标 | trafo_harmonic_thd |
| 直流快充桩数量多、功率大 | ev_dc_fast_charger_count, ev_charger_total_power |
| 充电桩绝缘告警 | ev_charger_insulation_alarm, ev_charger_fault_code |
| 光伏出力超过台区负荷（逆流） | pv_total_output, pv_reverse_power_flag, trafo_power_direction |
| 停电时刻为晴天正午（光伏高峰） | pv_irradiance |
| 储能模式切换时刻与停电时刻吻合 | ess_mode_switch_time, meter_outage_time |

---

### 3.5 施工外力破坏（3.2类，雄安高频原因）

| 判断规则 | 所需特征 |
|---------|---------|
| 台区附近有活跃施工工单 | construction_nearby_flag, construction_type |
| 施工位置与电缆路径空间交叉 | construction_location_overlap |
| 是否取得电力设施保护许可 | construction_permit_status |
| 施工时间与停电时刻重合 | construction_start_time, meter_outage_time |
| 管廊内振动告警（管廊内施工） | gallery_vibration_alarm_flag |

---

### 3.6 管廊渗水（3.1.2，雄安特有）

| 判断规则 | 所需特征 |
|---------|---------|
| 管廊积水液位告警 | gallery_water_level, gallery_water_alarm_flag |
| 停电前24小时有强降雨 | rainfall_24h |
| 零序电流突变特征 | trafo_zero_seq_current |

---

### 3.7 欠费停电和上级传导（4.2/5类）

| 判断规则 | 所需特征 |
|---------|---------|
| 用户欠费超阈值 | user_arrear_amount, user_arrear_days |
| 远程拉闸记录与停电时刻吻合 | remote_cutoff_flag, remote_cutoff_time |
| 同馈线多台区同时停电 | same_feeder_outage_area_cnt |
| 10kV馈线保护跳闸 | hv_feeder_breaker_status, hv_feeder_relay_action_time |
| 有对应计划停电工单 | planned_outage_flag, planned_outage_scope |

---

## 四、原始数据表设计

### 4.1 配变实时监测表（trafo_realtime）

| 字段名 | 类型 | 说明 |
|--------|------|------|
| trafo_id | VARCHAR | 配变编号 |
| record_time | DATETIME | 数据时刻（5分钟间隔） |
| ia / ib / ic | FLOAT | 三相电流（A） |
| ua / ub / uc | FLOAT | 三相电压（V） |
| i0 | FLOAT | 零序电流（A） |
| active_power | FLOAT | 有功功率（kW，正值=正向，负值=逆流） |
| load_rate | FLOAT | 负载率（%） |
| oil_temperature | FLOAT | 油温（℃） |
| thd_current | FLOAT | 电流谐波畸变率（%） |
| three_phase_imbalance | FLOAT | 三相不平衡度（%） |

---

### 4.2 用户停电事件表（meter_outage_event）

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

### 4.3 综合管廊监控表（pipe_gallery_monitor）【雄安特有】

| 字段名 | 类型 | 说明 |
|--------|------|------|
| gallery_id | VARCHAR | 管廊段编号 |
| record_time | DATETIME | 数据时刻（1分钟间隔） |
| temperature | FLOAT | 管廊内温度（℃） |
| humidity | FLOAT | 管廊内相对湿度（%） |
| water_level_cm | FLOAT | 积水液位（cm，0=无积水） |
| water_alarm | TINYINT | 渗水告警（0=正常，1=告警） |
| temp_alarm | TINYINT | 温度超限告警 |
| vibration_alarm | TINYINT | 振动告警（施工冲击感知） |
| gas_alarm | TINYINT | 有毒/可燃气体告警 |
| linked_cable_segment_id | VARCHAR | 关联的低压电缆段编号 |

---

### 4.4 充电桩运行数据表（ev_charger_status）【雄安特有】

| 字段名 | 类型 | 说明 |
|--------|------|------|
| charger_id | VARCHAR | 充电桩编号 |
| trafo_id | VARCHAR | 所属台区 |
| record_time | DATETIME | 数据时刻 |
| charger_type | VARCHAR | 充电桩类型（DC_FAST/AC_SLOW） |
| current_power_kw | FLOAT | 当前充电功率（kW） |
| charge_status | VARCHAR | 状态（idle/charging/fault） |
| fault_code | VARCHAR | 故障码（空=正常） |
| insulation_resistance | FLOAT | 绝缘电阻（kΩ，<500为告警） |
| harmonic_5th_current | FLOAT | 5次谐波电流（A，若支持） |

---

### 4.5 分布式光伏/储能数据表（distributed_energy_status）【雄安特有】

| 字段名 | 类型 | 说明 |
|--------|------|------|
| device_id | VARCHAR | 光伏/储能系统编号 |
| device_type | VARCHAR | 类型（PV_INVERTER/ESS_PCS） |
| trafo_id | VARCHAR | 所属台区 |
| record_time | DATETIME | 数据时刻 |
| output_power_kw | FLOAT | 输出功率（kW，正=发电/放电，负=充电） |
| energy_status | VARCHAR | 状态（generating/charging/discharging/idle/fault） |
| mode_change_time | DATETIME | 最近一次模式切换时刻 |
| fault_code | VARCHAR | 故障码（空=正常） |
| solar_irradiance | FLOAT | 当前太阳辐照度（W/m²，光伏适用） |

---

### 4.6 建设工程施工信息表（construction_work_order）【雄安特有】

| 字段名 | 类型 | 说明 |
|--------|------|------|
| work_order_id | VARCHAR | 施工工单编号 |
| work_type | VARCHAR | 施工类型（基础/顶管/明挖/管廊内施工） |
| contractor | VARCHAR | 施工单位 |
| location_wkt | TEXT | 施工范围（WKT格式地理坐标） |
| planned_start | DATETIME | 计划开始时间 |
| planned_end | DATETIME | 计划结束时间 |
| actual_start | DATETIME | 实际开始时间 |
| power_permit_flag | TINYINT | 是否取得电力设施保护许可（0=否，1=是） |
| proximity_to_cable_m | FLOAT | 与最近低压电缆的距离（米，GIS计算） |

---

### 4.7 计划停电工单表（planned_outage_order）

| 字段名 | 类型 | 说明 |
|--------|------|------|
| order_id | VARCHAR | 停电工单编号 |
| plan_start_time | DATETIME | 计划停电开始时间 |
| plan_end_time | DATETIME | 计划复电时间 |
| actual_start_time | DATETIME | 实际停电时间 |
| actual_end_time | DATETIME | 实际复电时间 |
| affected_feeder_list | TEXT | 涉及馈线列表（JSON） |
| affected_trafo_list | TEXT | 涉及台区列表（JSON） |
| notify_flag | TINYINT | 是否已通知用户 |

---

### 4.8 故障抢修工单表（repair_work_order）

| 字段名 | 类型 | 说明 |
|--------|------|------|
| order_id | VARCHAR | 工单编号 |
| trafo_id | VARCHAR | 涉及台区 |
| report_time | DATETIME | 报修时间 |
| restore_time | DATETIME | 复电时间 |
| fault_type | VARCHAR | 故障类型（现场判断） |
| fault_location | VARCHAR | 故障位置描述 |
| fault_cause | VARCHAR | 故障原因（现场填写） |
| field_notes | TEXT | 现场处理情况记录 |

---

## 五、数据优先级与获取难度（雄安场景版）

| 数据项 | 停电原因分析必要性 | 优先级 | 雄安获取难度 | 说明 |
|--------|----------------|--------|------------|------|
| 配变监测数据（TTU） | 高 | P0 | **低**（全覆盖） | 雄安TTU全覆盖，数据质量优于通用场景 |
| 用户停电事件（采集系统） | 高 | P0 | **低**（全覆盖） | 确定停电范围和时刻 |
| 10kV馈线状态（SCADA） | 高 | P0 | **低**（全覆盖） | 秒级实时数据 |
| 计划停电工单（OMS） | 高 | P0 | **低**（系统已有） | 计划停电核查 |
| **综合管廊监控数据** | 高 | P0 | **低**（雄安专项部署） | 雄安关键数据源，全覆盖 |
| **建设工程施工管理数据** | 高 | P0 | **中**（需跨部门对接） | 建设期高频原因，优先接入 |
| **充电桩管理数据** | 高 | P1 | **低**（充电运营商已有） | 充电桩谐波/过负荷判断 |
| **分布式能源管理数据** | 中 | P1 | **中**（多系统集成） | 光伏逆流、储能冲击判断 |
| 气象数据（降雨/雷电） | 中 | P1 | **低**（外部接入） | 管廊渗水和雷击判断 |
| 营销欠费数据 | 中 | P1 | **低**（系统已有） | 欠费停电判断 |
| 设备台账（PMS） | 中 | P1 | **低**（系统已有） | 设备年限、施工质量记录 |
| 电能质量监测数据 | 低 | P2 | **中**（专用仪器） | 充电桩谐波详细分析 |
| 雷电定位数据 | 低 | P2 | **低**（第三方接入） | 感应雷击辅助判断 |
