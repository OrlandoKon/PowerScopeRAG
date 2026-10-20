# Agentic RAG接入配置

## 1. 服务配置

```yaml
agentic_rag:
  base_url: "https://rag.orlando.ink"
  endpoint: "/rag"
  connect_timeout: 5
  read_timeout: 180
```

多智能体系统与RAG部署在同一服务器时，`base_url`可配置为`http://[::1]:8000`。公网域名当前使用IPv6，调用方需能访问IPv6网络。

健康检查：

```http
GET /health
```

## 2. 取证接口

```http
POST /rag
Content-Type: application/json
```

请求示例：

```json
{
  "query": "某400V台区全部停电，低压总开关过流动作，最大相电流492.5A。请检索停电原因判断所需的标准依据和相似案例。",
  "max_follow_up_rounds": 1,
  "knowledge_top_k": 4,
  "case_top_k": 3,
  "include_trace": false,
  "generate_answer": false
}
```

主要返回字段：

| 字段 | 用途 |
|---|---|
| `known_facts` | Agentic RAG识别出的已知事实 |
| `evidence` | 标准、论文、案例及计算证据 |
| `sufficient` | 当前证据是否足以支持后续研判 |
| `missing_data` | 仍需业务系统或现场补充的数据 |
| `conflicts` | 不同证据之间的冲突 |
| `coverage` | 各项研判要求对应的证据编号 |

多智能体系统应保留`evidence_id`和`source`用于结论引用；当`sufficient=false`时，先根据`missing_data`补充数据，再重新调用该接口。

## 3. Python调用

```python
import httpx


def retrieve_rag_evidence(task: str) -> dict:
    payload = {
        "query": task,
        "max_follow_up_rounds": 1,
        "knowledge_top_k": 4,
        "case_top_k": 3,
        "include_trace": False,
        "generate_answer": False,
    }
    timeout = httpx.Timeout(180.0, connect=5.0)
    with httpx.Client(
        base_url="https://rag.orlando.ink",
        timeout=timeout,
        trust_env=False,
    ) as client:
        response = client.post("/rag", json=payload)
        response.raise_for_status()
        return response.json()
```

调用后，将返回的`evidence`交给原因推理和交叉校核智能体；将`missing_data`交给数据采集或现场核查智能体。
