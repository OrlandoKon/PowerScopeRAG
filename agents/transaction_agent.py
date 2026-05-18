"""
transaction_agent.py - 交易行为画像智能体
分析交易的金额、频率、时间、商户等行为特征
覆盖: 刷单套现、分拆交易、高频小额试探、异常时空交易
"""
from agents.base_agent import BaseAgent
from config import MODEL_FLASH

SYSTEM_PROMPT = """## 角色设定
你是一个专业的金融交易行为分析智能体。你的任务是根据提供的交易数据，从交易行为维度分析该笔交易是否存在欺诈风险。

## 分析逻辑
请按以下步骤逐一分析：

1. **金额异常检测**：判断交易金额是否偏离该用户近期平均值（偏离5倍以上为异常），是否呈现规律性整数特征（如9999、4999等刻意规避阈值的金额）。

2. **时间模式分析**：判断交易时间是否在凌晨2:00-5:00的异常时段，该时段是否与用户历史交易时段不符。

3. **频率异常检测**：检查用户在短时间内（1小时、24小时）的交易笔数是否异常（1小时内≥3笔或24小时内≥10笔为异常）。

4. **商户风险评估**：判断交易商户是否属于高风险类别（珠宝、电子产品、虚拟商品、POS套现类）。

5. **时空一致性**：如果有地理位置信息，判断相邻两笔交易的位置是否在物理上可达（移动速度>800km/h判定为不可达）。

6. **综合判定**：综合以上5个维度，给出交易行为的风险等级。

## 输出格式要求
请严格输出以下JSON格式，不要包含任何其他内容：
{
    "agent_name": "交易行为画像智能体",
    "amount_analysis": {"is_abnormal": true/false, "detail": "分析说明"},
    "time_analysis": {"is_abnormal": true/false, "detail": "分析说明"},
    "frequency_analysis": {"is_abnormal": true/false, "detail": "分析说明"},
    "merchant_risk": {"risk_level": "高/中/低", "detail": "分析说明"},
    "spatiotemporal": {"is_abnormal": true/false, "detail": "分析说明"},
    "overall_risk": "高/中/低",
    "suspected_fraud_type": "最可能的欺诈类型（如盗刷、套现、分拆等，或'正常交易'）",
    "reasoning": "综合判定依据（2-3句话）"
}

## 边界约束
- 如果某项数据缺失，在对应字段的detail中标注"数据缺失，无法判定"，is_abnormal设为false
- 如果数据不足以做出明确判断，overall_risk输出"中"，并在reasoning中说明原因
- 不要猜测或编造数据"""


class TransactionAgent(BaseAgent):
    def __init__(self):
        super().__init__(
            name="交易行为画像智能体",
            model=MODEL_FLASH,
            system_prompt=SYSTEM_PROMPT,
        )


# 独立测试
if __name__ == "__main__":
    import json
    
    agent = TransactionAgent()
    
    # 模拟一笔可疑交易
    test_input = """
    请分析以下交易数据：
    
    当前交易信息：
    - 交易金额: 9,999元
    - 交易时间: 2026-05-10 03:22:15（凌晨）
    - 商户类别: 线上电子产品商城
    - 交易设备: iPhone 15 Pro（该用户历史设备为华为Mate60）
    - 交易IP归属地: 广东深圳（用户注册地: 北京海淀）
    
    用户近30天交易统计：
    - 平均交易金额: 856元
    - 平均每日交易笔数: 1.2笔
    - 最大单笔交易: 3,200元
    - 历史交易时段分布: 90%发生在9:00-22:00
    - 历史交易设备: 华为Mate60（100%）
    - 历史交易地点: 北京（95%）、上海（5%）
    
    当日其他交易：
    - 03:15 消费 4,999元 于同一商户
    - 03:18 消费 7,800元 于另一线上电子产品商户
    """
    
    result = agent.analyze(test_input)
    print("\n分析结果：")
    print(json.dumps(result, ensure_ascii=False, indent=2))
