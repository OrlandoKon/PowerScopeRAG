# 📊 金融反欺诈数据需求清单（Data Requirement Specification）

## 一、文档说明

本清单基于《欺诈风险分类树与判断逻辑清单》抽象生成，目标是将业务规则转化为**可计算特征与数据字段**，支撑：

* 风控模型训练
* 实时风控决策
* 特征工程体系建设
* 智能体（Agent）设计

---

## 二、总体结构设计

数据需求拆解为五层：

```
欺诈类型 → 规则 → 特征（Feature） → 原始字段（Field） → 数据源（Source）
```

---

## 三、核心特征字典（Feature Dictionary）

### 3.1 身份与设备特征

| 特征名                            | 含义           | 字段                       | 数据源    |
| ------------------------------ | ------------ | ------------------------ | ------ |
| device_30d_idcard_cnt          | 设备30天关联身份证数量 | device_id, id_card, time | 设备指纹系统 |
| device_30d_user_cnt            | 设备关联用户数      | device_id, user_id       | 用户系统   |
| device_history_match_flag      | 是否历史设备一致     | device_id, user_id       | 行为日志   |
| device_change_flag             | 设备是否突变       | device_id, login_time    | 行为日志   |
| ip_location_mismatch_flag      | IP与身份地不一致    | ip, id_card_region       | IP库    |
| multi_account_same_device_flag | 多账户共用设备      | device_id                | 设备指纹   |
| login_fail_then_success_flag   | 登录失败后成功      | login_log                | 登录系统   |

---

### 3.2 交易行为特征

| 特征名                          | 含义        | 字段       | 数据源  |
| ---------------------------- | --------- | -------- | ---- |
| txn_amt_ratio_30d            | 当前交易/历史均值 | txn_amt  | 交易系统 |
| txn_1h_cnt                   | 1小时交易次数   | txn_time | 交易系统 |
| txn_5min_cnt                 | 5分钟交易次数   | txn_time | 交易系统 |
| txn_amt_integer_pattern_flag | 整数金额特征    | txn_amt  | 交易系统 |
| txn_time_abnormal_flag       | 异常时间交易    | txn_time | 行为日志 |
| txn_location_distance        | 地理距离      | geo      | 地理服务 |
| txn_speed_kmh                | 移动速度      | geo+time | 计算   |
| multi_city_1d_flag           | 单日多城市交易   | location | 交易系统 |

---

### 3.3 信用与申请特征

| 特征名                    | 含义       | 字段                   | 数据源  |
| ---------------------- | -------- | -------------------- | ---- |
| apply_time_cost        | 表单填写耗时   | start_time, end_time | 前端日志 |
| apply_fast_flag        | 是否快速填写   | apply_time_cost      | 前端日志 |
| credit_inquiry_30d_cnt | 征信查询次数   | credit_log           | 征信   |
| loan_cnt               | 当前贷款数    | loan_table           | 信贷系统 |
| dti_ratio              | 负债收入比    | income, repayment    | 信贷系统 |
| first_loan_flag        | 是否首贷     | loan_history         | 信贷系统 |
| new_phone_flag         | 是否新手机号   | phone_time           | 运营商  |
| max_credit_usage_flag  | 是否申请最大额度 | credit_limit         | 信贷系统 |

---

### 3.4 资金流向特征

| 特征名                    | 含义      | 字段                | 数据源  |
| ---------------------- | ------- | ----------------- | ---- |
| fund_stay_time         | 资金停留时间  | in_time, out_time | 账户流水 |
| fund_in_out_ratio      | 资金进出比例  | amount            | 账户系统 |
| fan_out_degree         | 转出账户数   | transfer_to       | 账户系统 |
| fan_in_degree          | 转入来源数   | transfer_from     | 账户系统 |
| multi_transfer_24h_cnt | 24h转账次数 | transfer_log      | 账户系统 |
| fund_cycle_flag        | 是否循环转账  | graph             | 图计算  |

---

### 3.5 团伙与关联特征

| 特征名                      | 含义      | 字段            | 数据源  |
| ------------------------ | ------- | ------------- | ---- |
| device_shared_cnt        | 设备共享账户数 | device_id     | 图谱系统 |
| ip_shared_cnt            | IP共享账户数 | ip            | 图谱系统 |
| contact_overlap_cnt      | 联系人重合数  | contact_list  | 通讯数据 |
| graph_cluster_flag       | 是否团伙    | graph_id      | 图计算  |
| account_similarity_score | 行为相似度   | multi_feature | 特征计算 |
| central_account_flag     | 资金中心账户  | graph         | 图计算  |

---

## 四、规则与特征映射示例

### 4.1 身份冒用欺诈（盗用身份）

| 规则        | 特征                        |
| --------- | ------------------------- |
| 多身份绑定设备   | device_30d_idcard_cnt     |
| 手机号新注册    | new_phone_flag            |
| IP与身份地不一致 | ip_location_mismatch_flag |
| 表单填写过快    | apply_fast_flag           |
| 关联欺诈账户    | device_shared_cnt         |

---

### 4.2 信用卡盗刷

| 规则    | 特征                        |
| ----- | ------------------------- |
| 金额异常  | txn_amt_ratio_30d         |
| 设备不一致 | device_history_match_flag |
| 地理异常  | txn_location_distance     |
| 高频交易  | txn_1h_cnt                |
| 凌晨交易  | txn_time_abnormal_flag    |

---

### 4.3 刷单套现

| 规则   | 特征                           |
| ---- | ---------------------------- |
| 整数金额 | txn_amt_integer_pattern_flag |
| 高频交易 | txn_1h_cnt                   |
| 高频退款 | refund_ratio                 |
| 商户风险 | mcc_risk_flag                |

---

### 4.4 快进快出（洗钱）

| 规则    | 特征                     |
| ----- | ---------------------- |
| 停留时间短 | fund_stay_time         |
| 金额接近  | fund_in_out_ratio      |
| 多次重复  | multi_transfer_24h_cnt |

---

### 4.5 团伙欺诈

| 规则     | 特征                                |
| ------ | --------------------------------- |
| 同设备/IP | device_shared_cnt / ip_shared_cnt |
| 联系人重叠  | contact_overlap_cnt               |
| 行为一致   | account_similarity_score          |

---

## 五、原始数据表设计

### 5.1 用户信息表（user_profile）

* user_id
* id_card
* phone
* register_time
* address
* income
* company

---

### 5.2 设备日志表（device_log）

* device_id
* user_id
* ip
* device_type
* device_info
* login_time

---

### 5.3 交易表（transaction）

* txn_id
* user_id
* txn_amt
* txn_time
* merchant_id
* location
* mcc

---

### 5.4 账户流水表（account_flow）

* account_id
* in_amt
* out_amt
* in_time
* out_time
* counterparty

---

### 5.5 征信数据表（credit_profile）

* user_id
* inquiry_cnt
* loan_cnt
* repayment_amt
* income

---

### 5.6 图谱关系表（graph_edge）

* src_user
* dst_user
* relation_type（设备/IP/联系人）

---

## 六、数据优先级与缺口分析

| 数据项       | 必要性 | 优先级 | 说明      |
| --------- | --- | --- | ------- |
| device_id | 必须  | P0  | 核心反欺诈特征 |
| IP归属地     | 必须  | P0  | 地理检测    |
| 交易数据      | 必须  | P0  | 行为分析    |
| 征信数据      | 高   | P1  | 信用评估    |
| 联系人数据     | 高   | P1  | 团伙识别    |
| GPS定位     | 可选  | P2  | 精细风控    |
| 社保数据      | 可选  | P2  | 信息验证    |

---