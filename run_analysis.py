"""
run_analysis.py - 多智能体协同分析入口
演示完整的 交易数据 → 多智能体并行分析 → 综合评估 流程

当前版本: MVP（仅包含交易行为画像 + 综合评估两个智能体）
后续迭代: 逐步加入信用画像、身份核验、资金流向、关联网络智能体
"""
import json
import time
from agents.transaction_agent import TransactionAgent
from agents.comprehensive_agent import ComprehensiveAgent


def format_transaction_for_agent(txn: dict) -> str:
    """将交易数据格式化为智能体的输入文本"""
    data = txn["transaction_data"]
    profile = txn["user_profile"]
    same_day = txn.get("same_day_transactions", [])
    
    text = f"""请分析以下交易数据：

当前交易信息：
- 交易ID: {txn['transaction_id']}
- 交易金额: {data['amount']}元
- 交易时间: {data['timestamp']}
- 商户类别: {data['merchant_category']}
- 商户名称: {data['merchant_name']}
- 交易设备: {data['device']}
- 交易IP归属地: {data['ip_location']}

用户画像：
- 注册时间: {profile['registration_date']}
- 注册城市: {profile['registered_city']}
- 信用评分: {profile['credit_score']}
- 历史常用设备: {profile['historical_device']}
- 近30天平均交易金额: {profile['avg_transaction_amount_30d']}元
- 近30天最大单笔交易: {profile['max_transaction_amount_30d']}元
- 日均交易笔数: {profile['avg_daily_transactions']}
- 活跃时段: {profile['active_hours']}
- 常见交易地点: {profile['historical_locations']}
- 未结清贷款笔数: {profile['outstanding_loans']}
- 近30天征信查询次数: {profile['recent_credit_inquiries_30d']}"""
    
    if same_day:
        text += "\n\n当日其他交易："
        for t in same_day:
            text += f"\n- {t['time']} 消费 {t['amount']}元 于 {t['merchant']}"
    
    return text


def run_single_analysis(txn: dict):
    """对单笔交易执行完整的多智能体分析"""
    print(f"\n{'='*60}")
    print(f"分析交易: {txn['transaction_id']}")
    print(f"描述: {txn['description']}")
    print(f"{'='*60}")
    
    # 格式化输入数据
    formatted_input = format_transaction_for_agent(txn)
    
    # ========== 第一层：画像智能体并行分析 ==========
    # （MVP版本目前只有交易行为画像，后续加入其他4个）
    print("\n--- 第一层：画像智能体分析 ---")
    
    start_time = time.time()
    
    transaction_result = TransactionAgent().analyze(formatted_input)
    
    # TODO: 后续加入以下智能体的并行调用
    # credit_result = CreditAgent().analyze(formatted_input)
    # identity_result = IdentityAgent().analyze(formatted_input)
    # fund_flow_result = FundFlowAgent().analyze(formatted_input)
    # network_result = NetworkAgent().analyze(formatted_input)
    
    layer1_time = time.time() - start_time
    print(f"\n  第一层耗时: {layer1_time:.1f}秒")
    
    # ========== 第二层：综合风险评估 ==========
    print("\n--- 第二层：综合风险评估 ---")
    
    # 汇总各维度结果，送入综合分析智能体
    comprehensive_input = f"""请根据以下各维度分析结果，进行综合风险评估。

## 交易行为画像智能体的分析结果：
{json.dumps(transaction_result, ensure_ascii=False, indent=2)}

## 用户信用画像智能体的分析结果：
（该维度数据暂缺，待后续接入）

## 身份核验画像智能体的分析结果：
（该维度数据暂缺，待后续接入）

## 资金流向分析智能体的分析结果：
（该维度数据暂缺，待后续接入）

## 关联网络分析智能体的分析结果：
（该维度数据暂缺，待后续接入）

## 原始交易数据（供参考）：
{formatted_input}"""
    
    start_time = time.time()
    comprehensive_result = ComprehensiveAgent().analyze(comprehensive_input)
    layer2_time = time.time() - start_time
    print(f"\n  第二层耗时: {layer2_time:.1f}秒")
    
    # ========== 输出最终结果 ==========
    print(f"\n{'='*60}")
    print("📊 最终分析结果")
    print(f"{'='*60}")
    print(json.dumps(comprehensive_result, ensure_ascii=False, indent=2))
    
    # 与预期标签对比
    expected = txn.get("expected_type", "未知")
    actual = comprehensive_result.get("fraud_type", "未知")
    match = "✅" if expected in actual or actual in expected else "⚠️"
    print(f"\n{match} 预期类型: {expected} | 系统判定: {actual}")
    print(f"⏱️ 总耗时: {layer1_time + layer2_time:.1f}秒")
    
    return comprehensive_result


def main():
    """主函数：加载样本数据并逐一分析"""
    print("=" * 60)
    print("PowerScopeRAG - 金融风控多智能体欺诈分析系统")
    print("模型: DeepSeek-V4-Flash (画像) + DeepSeek-V4-Pro (综合)")
    print("=" * 60)
    
    # 加载样本数据
    with open("data/sample_transactions.json", "r", encoding="utf-8") as f:
        transactions = json.load(f)
    
    print(f"\n共加载 {len(transactions)} 笔待分析交易")
    
    results = []
    for txn in transactions:
        result = run_single_analysis(txn)
        results.append({
            "transaction_id": txn["transaction_id"],
            "expected": txn.get("expected_type"),
            "result": result,
        })
    
    # 汇总统计
    print(f"\n\n{'='*60}")
    print("📋 分析汇总")
    print(f"{'='*60}")
    for r in results:
        risk = r["result"].get("final_risk_level", "未知")
        fraud_type = r["result"].get("fraud_type", "未知")
        action = r["result"].get("recommended_action", "未知")
        print(f"  {r['transaction_id']} | 风险:{risk} | 类型:{fraud_type} | 建议:{action}")


if __name__ == "__main__":
    main()
