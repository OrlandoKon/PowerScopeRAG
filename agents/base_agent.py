"""
base_agent.py - 智能体基类
所有画像智能体和综合分析智能体继承此类
"""
import json
from openai import OpenAI
from config import DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL, DEFAULT_TEMPERATURE, DEFAULT_TOP_P


class BaseAgent:
    """智能体基类，封装DeepSeek V4 API调用"""
    
    def __init__(self, name: str, model: str, system_prompt: str):
        self.name = name
        self.model = model
        self.system_prompt = system_prompt
        self.client = OpenAI(
            api_key=DEEPSEEK_API_KEY,
            base_url=DEEPSEEK_BASE_URL,
        )
    
    def analyze(self, user_input: str, use_thinking: bool = False) -> dict:
        """
        调用大模型进行分析
        
        Args:
            user_input: 输入的交易数据或上游智能体的输出
            use_thinking: 是否开启思考模式（复杂推理时建议开启）
        
        Returns:
            dict: 解析后的JSON结果
        """
        print(f"  [{self.name}] 正在分析...")
        
        kwargs = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": self.system_prompt},
                {"role": "user", "content": user_input},
            ],
            "temperature": DEFAULT_TEMPERATURE,
            "top_p": DEFAULT_TOP_P,
            "response_format": {"type": "json_object"},
        }
        
        if use_thinking:
            kwargs["extra_body"] = {"thinking": {"type": "enabled"}}
        
        try:
            response = self.client.chat.completions.create(**kwargs)
            raw_output = response.choices[0].message.content
            result = json.loads(raw_output)
            
            print(f"  [{self.name}] ✅ 分析完成 (tokens: {response.usage.prompt_tokens}+{response.usage.completion_tokens})")
            return result
            
        except json.JSONDecodeError:
            print(f"  [{self.name}] ⚠️ JSON解析失败，返回原始文本")
            return {"raw_output": raw_output, "parse_error": True}
        except Exception as e:
            print(f"  [{self.name}] ❌ 调用失败: {e}")
            return {"error": str(e)}
