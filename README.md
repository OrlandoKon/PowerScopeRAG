# PowerScopeRAG 项目快速启动指南

## 第一步：注册 DeepSeek API Key

1. 访问 https://platform.deepseek.com 注册账号
2. 进入「充值」页面，充值最低 2 美元（约 15 元人民币）
3. 进入「API Keys」页面，点击「创建新 API Key」
4. 复制 Key（只显示一次，务必保存）

## 第二步：安装依赖

```bash
pip install openai
```

## 第三步：设置环境变量

```bash
# Linux/Mac
export DEEPSEEK_API_KEY="sk-你的key"

# Windows PowerShell
$env:DEEPSEEK_API_KEY="sk-你的key"
```

## 第四步：运行测试

```bash
python test_api.py           # 测试API连通性
python agents/transaction_agent.py   # 单个智能体测试
python run_analysis.py       # 完整多智能体分析
```

## 项目结构

```
PowerScopeRAG/
├── config.py                 # API配置
├── test_api.py               # API连通性测试
├── agents/
│   ├── __init__.py
│   ├── base_agent.py         # 智能体基类
│   ├── transaction_agent.py  # 交易行为画像智能体
│   ├── credit_agent.py       # 用户信用画像智能体
│   ├── identity_agent.py     # 身份核验画像智能体
│   ├── fund_flow_agent.py    # 资金流向分析智能体
│   ├── network_agent.py      # 关联网络分析智能体
│   └── comprehensive_agent.py # 综合风险评估智能体
├── run_analysis.py           # 多智能体协同分析入口
├── doc/                      # 知识库文档（已完成）
│   ├── regulations/
│   ├── cases/
│   ├── industry/
│   └── data_dict/
└── data/                     # 数据集
    └── sample_transactions.json
```

## 模型选择

| 智能体 | 推荐模型 | 原因 |
|--------|---------|------|
| 交易行为、信用、身份、关联网络 | deepseek-v4-flash | 画像归纳类任务，Flash够用且便宜 |
| 资金流向分析 | deepseek-v4-flash | 模式匹配类任务 |
| 综合风险评估 | deepseek-v4-pro | 需要强推理，交叉比对多维度结论 |

## API 关键参数

- base_url: https://api.deepseek.com/v1
- 模型名: deepseek-v4-pro 或 deepseek-v4-flash
- temperature=1.0, top_p=1.0（官方推荐默认值）
- 思考模式: extra_body={"thinking": {"type": "enabled"}}（复杂推理时开启）
- 上下文窗口: 1M tokens（百万级）
- 兼容 OpenAI SDK，无需安装额外包
