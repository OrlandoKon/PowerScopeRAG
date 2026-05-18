"""
test_api.py - 测试 DeepSeek V4 API 连通性
运行: python test_api.py
"""
from openai import OpenAI
from config import DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL, MODEL_FLASH, MODEL_PRO

def test_connection():
    client = OpenAI(
        api_key=DEEPSEEK_API_KEY,
        base_url=DEEPSEEK_BASE_URL,
    )
    
    print("=" * 50)
    print("PowerScopeRAG - DeepSeek V4 API 连通性测试")
    print("=" * 50)
    
    # 测试1: Flash模型基础调用
    print("\n[测试1] 调用 deepseek-v4-flash ...")
    try:
        response = client.chat.completions.create(
            model=MODEL_FLASH,
            messages=[
                {"role": "system", "content": "你是一个金融风控专家。"},
                {"role": "user", "content": "请用一句话解释什么是信用卡盗刷。"}
            ],
            temperature=1.0,
            top_p=1.0,
        )
        print(f"  ✅ 成功! 模型回复: {response.choices[0].message.content[:100]}")
        print(f"  Token消耗: 输入{response.usage.prompt_tokens} + 输出{response.usage.completion_tokens}")
    except Exception as e:
        print(f"  ❌ 失败: {e}")
        return
    
    # 测试2: Pro模型 + 思考模式
    print(f"\n[测试2] 调用 deepseek-v4-pro (思考模式) ...")
    try:
        response = client.chat.completions.create(
            model=MODEL_PRO,
            messages=[
                {"role": "system", "content": "你是一个金融风控专家。"},
                {"role": "user", "content": "一笔交易金额9999元，凌晨3点发生，设备是从未见过的新设备。这可能是什么类型的欺诈？请简要分析。"}
            ],
            temperature=1.0,
            top_p=1.0,
            extra_body={"thinking": {"type": "enabled"}},
        )
        print(f"  ✅ 成功! 模型回复: {response.choices[0].message.content[:200]}")
        print(f"  Token消耗: 输入{response.usage.prompt_tokens} + 输出{response.usage.completion_tokens}")
    except Exception as e:
        print(f"  ❌ 失败: {e}")
        return
    
    # 测试3: JSON格式输出
    print(f"\n[测试3] 测试JSON格式输出 ...")
    try:
        response = client.chat.completions.create(
            model=MODEL_FLASH,
            messages=[
                {"role": "system", "content": "你是一个金融风控分析系统。请严格按JSON格式输出，不要包含任何其他内容。"},
                {"role": "user", "content": '分析以下交易的风险等级。交易信息：金额9999元，时间凌晨3:00，设备为新设备。请输出JSON：{"risk_level": "高/中/低", "reason": "原因"}'}
            ],
            temperature=1.0,
            top_p=1.0,
            response_format={"type": "json_object"},
        )
        result = response.choices[0].message.content
        print(f"  ✅ 成功! JSON输出: {result[:200]}")
        
        # 验证是否为有效JSON
        import json
        parsed = json.loads(result)
        print(f"  ✅ JSON解析成功: {parsed}")
    except Exception as e:
        print(f"  ❌ 失败: {e}")
    
    print("\n" + "=" * 50)
    print("所有测试完成！API连接正常，可以开始开发智能体。")
    print("=" * 50)

if __name__ == "__main__":
    test_connection()
