"""Evaluate Alibaba Cloud Model Studio knowledge retrieval.

``aid-`` application IDs use the Knowledge Q&A SSE API. Other IDs use the
Responses API ``file_search`` tool. In both modes, the script scores only the
ranked retrieval results and checkpoints every completed question.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import sys
import time
import unicodedata
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests
from openai import OpenAI

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

CASES_FILE = ROOT / "tests" / "test_cases.json"
CACHE_FILE = ROOT / "tests" / "bailian_retrieval_cache.json"
REPORTS_DIR = ROOT / "tests" / "reports"
KNOWLEDGE_BASE_ID_FILE = ROOT / "aliragid.txt"
DEFAULT_MODEL = "qwen3.5-flash"  # Used only by Responses API file_search mode.
DEFAULT_AGENT_MODEL_LABEL = "configured-in-bailian-agent"


class BailianAPIError(RuntimeError):
    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def load_credentials(csv_path: Path | None) -> tuple[str, str, str]:
    api_key = os.environ.get("DASHSCOPE_API_KEY", "")
    base_url = os.environ.get("BAILIAN_BASE_URL", "")
    workspace_id = os.environ.get("BAILIAN_WORKSPACE_ID", "")
    values: dict[str, str] = {}

    if csv_path and csv_path.exists():
        with csv_path.open(encoding="utf-8-sig", newline="") as f:
            values = {
                row[0].strip(): row[1].strip()
                for row in csv.reader(f)
                if len(row) >= 2
            }
        api_key = api_key or values.get("apiKey", "")
        base_url = base_url or values.get("openAiCompatible", "")
        workspace_id = workspace_id or values.get("workspaceId", "")

    if not api_key:
        raise RuntimeError("未找到DASHSCOPE_API_KEY或CSV中的apiKey")
    return api_key, base_url.rstrip("/"), workspace_id


def load_knowledge_base_id(cli_value: str) -> str:
    value = cli_value.strip()
    for env_name in (
        "BAILIAN_RESOURCE_ID",
        "BAILIAN_AGENT_ID",
        "BAILIAN_KNOWLEDGE_BASE_ID",
    ):
        value = value or os.environ.get(env_name, "").strip()
    if not value and KNOWLEDGE_BASE_ID_FILE.exists():
        value = KNOWLEDGE_BASE_ID_FILE.read_text(encoding="utf-8").strip()
    if "=" in value:
        value = value.split("=", 1)[1].strip()
    return value.strip("\"'")


def normalize_document_name(name: str) -> str:
    """Match uploaded PDF/DOCX names against expected Markdown source names."""
    basename = re.split(r"[/\\]", name)[-1]
    basename = re.sub(r"\.(pdf|docx?|md|txt)$", "", basename, flags=re.IGNORECASE)
    basename = unicodedata.normalize("NFKC", basename).lower()
    return re.sub(r"\s+", "", basename)


def is_expected(filename: str, expected_sources: list[str]) -> bool:
    actual = normalize_document_name(filename)
    return any(actual == normalize_document_name(source) for source in expected_sources)


def extract_file_search(response: Any) -> tuple[list[dict], str]:
    results: list[dict] = []
    for item in response.output:
        item_type = getattr(item, "type", None)
        if item_type != "file_search_call":
            continue
        for result in getattr(item, "results", None) or []:
            results.append({
                "file_id": getattr(result, "file_id", ""),
                "filename": getattr(result, "filename", ""),
                "score": float(getattr(result, "score", 0.0)),
                "text": getattr(result, "text", ""),
            })
    return results, response.output_text


def resource_key(resource_id: str) -> str:
    return hashlib.sha256(resource_id.encode("utf-8")).hexdigest()[:12]


def save_cache(data: dict[str, dict]) -> None:
    ordered = sorted(data.values(), key=lambda row: row.get("id", ""))
    tmp = CACHE_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(ordered, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(CACHE_FILE)


def agent_api_url(workspace_id: str, compatible_base_url: str) -> str:
    if not workspace_id and compatible_base_url:
        hostname = urlparse(compatible_base_url).hostname or ""
        workspace_id = hostname.split(".", 1)[0]
    if not workspace_id:
        raise RuntimeError("调用aid-应用需要CSV中的workspaceId或BAILIAN_WORKSPACE_ID")
    return (
        f"https://{workspace_id}.cn-beijing.maas.aliyuncs.com"
        "/api/v2/apps/knowledge/chat"
    )


def _json_value(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value


def _find_nested_value(value: Any, keys: tuple[str, ...]) -> Any:
    if isinstance(value, dict):
        for key in keys:
            candidate = value.get(key)
            if candidate not in (None, "", [], {}):
                return candidate
        for candidate in value.values():
            found = _find_nested_value(candidate, keys)
            if found not in (None, "", [], {}):
                return found
    elif isinstance(value, list):
        for candidate in value:
            found = _find_nested_value(candidate, keys)
            if found not in (None, "", [], {}):
                return found
    return None


def normalize_agent_doc(doc: dict[str, Any]) -> dict[str, Any]:
    filename = _find_nested_value(doc, (
        "file_name",
        "filename",
        "document_name",
        "doc_name",
        "source_name",
        "source",
        "title",
    ))
    score = _find_nested_value(doc, (
        "rerank_score",
        "relevance_score",
        "score",
        "similarity_score",
    ))
    text = _find_nested_value(doc, (
        "text",
        "chunk_content",
        "page_content",
        "content",
    ))
    file_id = _find_nested_value(doc, ("file_id", "document_id", "doc_id"))
    try:
        score = float(score)
    except (TypeError, ValueError):
        score = 0.0
    return {
        "file_id": str(file_id or ""),
        "filename": str(filename or ""),
        "score": score,
        "text": str(text or ""),
    }


def iter_sse(response: requests.Response):
    event = "message"
    data_lines: list[str] = []
    for raw_line in response.iter_lines():
        line = raw_line.decode("utf-8") if isinstance(raw_line, bytes) else raw_line
        if not line:
            if data_lines:
                yield event, "\n".join(data_lines)
            event = "message"
            data_lines = []
        elif line.startswith("event:"):
            event = line[6:].strip()
        elif line.startswith("data:"):
            data_lines.append(line[5:].lstrip())
    if data_lines:
        yield event, "\n".join(data_lines)


def message_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            str(part.get("text", ""))
            for part in content
            if isinstance(part, dict) and part.get("type") == "text"
        )
    return ""


def extract_agent_stream(
    response: requests.Response,
    stop_after_retrieval: bool = True,
) -> tuple[list[dict], str, bool]:
    tool_names: dict[str, str] = {}
    raw_docs: list[dict[str, Any]] = []
    answer_parts: list[str] = []
    retrieval_called = False

    for event, data in iter_sse(response):
        if data.strip() == "[DONE]":
            continue
        try:
            payload = json.loads(data)
        except json.JSONDecodeError as exc:
            raise BailianAPIError(f"无法解析百炼SSE响应：{exc}") from exc

        code = payload.get("status_code") or payload.get("code")
        if event == "error" or str(code) not in {"", "0", "200", "None"}:
            message = payload.get("message") or "百炼SSE返回错误"
            raise BailianAPIError(f"{code}: {message}", 500)

        choices = (payload.get("output") or {}).get("choices") or []
        for choice in choices:
            message = choice.get("message") or {}
            extra = message.get("extra") or {}
            for call in message.get("tool_calls") or []:
                function = call.get("function") or {}
                name = function.get("name") or call.get("name") or ""
                call_id = call.get("id") or call.get("index") or ""
                if call_id:
                    tool_names[str(call_id)] = str(name)
                if name == "semantic_search":
                    retrieval_called = True

            extra_json = _json_value(
                (message.get("additional_kwargs") or {}).get("extra_json")
            )
            docs = _json_value(extra_json.get("docs")) if isinstance(extra_json, dict) else None
            tool_name = tool_names.get(str(message.get("tool_call_id") or ""), "")
            if isinstance(docs, list) and (tool_name in {"", "semantic_search"}):
                retrieval_called = True
                raw_docs.extend(doc for doc in docs if isinstance(doc, dict))
                if raw_docs and stop_after_retrieval:
                    return [normalize_agent_doc(doc) for doc in raw_docs], "", True

            if extra.get("step") == "generating":
                answer_parts.append(message_text(message.get("content")))

    results = [normalize_agent_doc(doc) for doc in raw_docs]
    return results, "".join(answer_parts), retrieval_called


def call_agent(
    api_url: str,
    api_key: str,
    agent_id: str,
    question: str,
    include_answer: bool = False,
) -> tuple[list[dict], str, bool]:
    with requests.post(
        api_url,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
        },
        json={
            "input": {
                "messages": [{
                    "role": "user",
                    "content": [{"type": "text", "text": question}],
                }],
            },
            "parameters": {"agent_options": {"agent_id": agent_id}},
            "stream": True,
        },
        stream=True,
        timeout=(10, 180),
    ) as response:
        if not response.ok:
            detail = response.text[:500].strip()
            raise BailianAPIError(
                f"百炼HTTP {response.status_code}: {detail}",
                response.status_code,
            )
        return extract_agent_stream(response, stop_after_retrieval=not include_answer)


def retrieve_all(
    cases: list[dict],
    resource_id: str,
    mode: str,
    model: str,
    fetch: Any,
    refresh: bool = False,
    require_answer: bool = False,
    workers: int = 1,
) -> list[dict]:
    current_ids = {case["id"] for case in cases}
    key = resource_key(resource_id)
    cached = {
        row["id"]: row
        for row in load_json(CACHE_FILE, [])
        if row.get("id")
    }
    existing = {} if refresh else {
        case_id: row
        for case_id, row in cached.items()
        if case_id in current_ids
        and row.get("completed") is True
        and row.get("mode") == mode
        and row.get("resource_key") == key
        and (not require_answer or row.get("answer_complete") is True)
    }
    todo = [case for case in cases if case["id"] not in existing]
    print(
        f"百炼待检索：{len(todo)}题，已缓存：{len(existing)}题，"
        f"并发数：{workers}"
    )

    def process_case(case: dict) -> tuple[dict | None, float, int, Exception | None]:
        started = time.time()
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                results, answer, retrieval_called = fetch(case["question"])
                row = {
                    "id": case["id"],
                    "question": case["question"],
                    "expected_sources": case["expected_sources"],
                    "results": results,
                    "answer": answer,
                    "answer_complete": require_answer,
                    "model": model,
                    "mode": mode,
                    "resource_key": key,
                    "retrieval_called": retrieval_called,
                    "completed": True,
                }
                return row, time.time() - started, attempt, None
            except Exception as exc:
                last_error = exc
                if getattr(exc, "status_code", None) in {400, 401, 403, 404}:
                    break
                if attempt < 2:
                    time.sleep(5)
        return None, time.time() - started, 2, last_error

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(process_case, case): case for case in todo}
        for completed, future in enumerate(as_completed(futures), len(existing) + 1):
            case = futures[future]
            row, elapsed, retries, error = future.result()
            prefix = f"  [{completed}/{len(cases)}] {case['id']}... "
            if row is None:
                print(f"{prefix}失败：{error}", flush=True)
                continue
            cached[case["id"]] = row
            existing[case["id"]] = row
            save_cache(cached)
            results = row["results"]
            top1 = results[0]["filename"] if results else "（无）"
            retry_text = f"，重试{retries}次" if retries else ""
            print(
                f"{prefix}{len(results)}条，Top1={top1}，{elapsed:.1f}s{retry_text}",
                flush=True,
            )

    return [existing[case["id"]] for case in cases if case["id"] in existing]


def file_search_fetcher(client: OpenAI, knowledge_base_id: str, model: str):
    def fetch(question: str) -> tuple[list[dict], str, bool]:
        response = client.responses.create(
            model=model,
            input=question,
            include=["file_search_call.results"],
            tools=[{
                "type": "file_search",
                "vector_store_ids": [knowledge_base_id],
            }],
            tool_choice="required",
            reasoning={"effort": "none"},
            temperature=0.0,
            max_output_tokens=32,
            store=False,
        )
        results, answer = extract_file_search(response)
        retrieval_called = any(
            getattr(item, "type", None) == "file_search_call"
            for item in response.output
        )
        return results, answer, retrieval_called

    return fetch


def score_row(row: dict) -> dict:
    filenames = [result["filename"] for result in row["results"]]
    expected = row["expected_sources"]
    rank = next(
        (index for index, name in enumerate(filenames, 1) if is_expected(name, expected)),
        None,
    )
    return {
        "id": row["id"],
        "hit_at_1": rank is not None and rank <= 1,
        "hit_at_3": rank is not None and rank <= 3,
        "hit_at_5": rank is not None and rank <= 5,
        "mrr": 1.0 / rank if rank is not None else 0.0,
        "rank": rank,
        "retrieval_called": row.get("retrieval_called", bool(row["results"])),
        "result_count": len(row["results"]),
        "retrieved_sources": filenames[:5],
    }


def save_report(
    rows: list[dict],
    scores: list[dict],
    elapsed: float,
    mode: str,
) -> Path:
    n = len(scores)
    expected_names = {
        normalize_document_name(name): name
        for row in rows
        for name in row["expected_sources"]
    }
    retrieved_names = {
        normalize_document_name(result["filename"]): result["filename"]
        for row in rows
        for result in row["results"]
        if result["filename"]
    }
    summary = {
        "hit_at_1": sum(row["hit_at_1"] for row in scores) / n,
        "hit_at_3": sum(row["hit_at_3"] for row in scores) / n,
        "hit_at_5": sum(row["hit_at_5"] for row in scores) / n,
        "mrr": sum(row["mrr"] for row in scores) / n,
    }
    report = {
        "timestamp": datetime.now().isoformat(),
        "mode": mode,
        "n": n,
        "elapsed_s": round(elapsed, 1),
        "summary": {key: round(value, 4) for key, value in summary.items()},
        "rank_distribution": dict(sorted(Counter(
            str(row["rank"]) if row["rank"] is not None else "not_in_top5"
            for row in scores
        ).items())),
        "source_audit": {
            "expected_source_count": len(expected_names),
            "retrieved_source_count": len(retrieved_names),
            "expected_never_retrieved": sorted(
                expected_names[key] for key in expected_names.keys() - retrieved_names.keys()
            ),
            "retrieved_not_expected": sorted(
                retrieved_names[key] for key in retrieved_names.keys() - expected_names.keys()
            ),
        },
        "per_question": scores,
    }
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = REPORTS_DIR / f"bailian_retrieval_report_{timestamp}.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def main() -> None:
    parser = argparse.ArgumentParser(description="阿里云百炼知识库Hit@K/MRR评测")
    parser.add_argument(
        "--resource-id",
        "--knowledge-base-id",
        dest="resource_id",
        default="",
        help="百炼应用ID(aid-)或知识库ID；默认读取环境变量或根目录aliragid.txt",
    )
    parser.add_argument(
        "--credentials-csv",
        type=Path,
        default=ROOT / "默认业务空间-apiKey-.csv",
    )
    parser.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help="file_search模式的生成模型；aid-应用模式由百炼控制台配置",
    )
    parser.add_argument(
        "--agent-model-label",
        default=DEFAULT_AGENT_MODEL_LABEL,
        help="仅记录aid-应用在百炼控制台配置的模型名称，不会改变应用模型",
    )
    parser.add_argument("--ids", nargs="+", help="只评测指定题号")
    parser.add_argument(
        "--include-answer",
        action="store_true",
        help="等待并缓存应用最终回答；纯检索评测默认在取得切片后结束",
    )
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="忽略所选题目的已有缓存并重新调用百炼",
    )
    parser.add_argument("--skip-api", action="store_true", help="只读取缓存并重新统计")
    parser.add_argument("--workers", type=int, default=1, help="并发请求数（默认1）")
    args = parser.parse_args()

    if args.workers < 1:
        parser.error("--workers必须大于等于1")

    resource_id = load_knowledge_base_id(args.resource_id)
    if not resource_id and not args.skip_api:
        parser.error("请通过--resource-id、环境变量或aliragid.txt提供百炼资源ID")

    cases: list[dict] = load_json(CASES_FILE, [])
    if args.ids:
        requested = set(args.ids)
        cases = [case for case in cases if case["id"] in requested]
    if not cases:
        parser.error("没有可评测题目")

    started = time.time()
    mode = "bailian_cached"
    if args.skip_api:
        cached = {row["id"]: row for row in load_json(CACHE_FILE, [])}
        rows = [cached[case["id"]] for case in cases if case["id"] in cached]
        if rows:
            mode = rows[0].get("mode", mode)
    else:
        api_key, base_url, workspace_id = load_credentials(args.credentials_csv)
        if resource_id.startswith("aid-"):
            mode = "bailian_knowledge_chat_agent"
            answer_model_label = args.agent_model_label
            url = agent_api_url(workspace_id, base_url)
            fetch = lambda question: call_agent(
                url,
                api_key,
                resource_id,
                question,
                include_answer=args.include_answer,
            )
        else:
            if not base_url:
                parser.error("file_search模式需要BAILIAN_BASE_URL或CSV中的openAiCompatible")
            mode = "bailian_file_search"
            answer_model_label = args.model
            client = OpenAI(
                api_key=api_key,
                base_url=base_url,
                timeout=90.0,
                max_retries=0,
            )
            fetch = file_search_fetcher(client, resource_id, args.model)
        rows = retrieve_all(
            cases,
            resource_id,
            mode,
            answer_model_label,
            fetch,
            refresh=args.refresh,
            require_answer=args.include_answer,
            workers=args.workers,
        )

    if not rows:
        parser.error("没有可统计的百炼检索结果")
    scores = [score_row(row) for row in rows]
    report_path = save_report(rows, scores, time.time() - started, mode)
    report = json.loads(report_path.read_text(encoding="utf-8"))
    print("\n百炼检索结果：")
    for name, value in report["summary"].items():
        print(f"  {name}: {value:.4f}")
    print(f"  完成题数：{len(rows)}/{len(cases)}")
    audit = report["source_audit"]
    if audit["expected_never_retrieved"]:
        print(f"  从未召回的期望来源：{audit['expected_never_retrieved']}")
    if audit["retrieved_not_expected"]:
        print(f"  题库外召回来源：{audit['retrieved_not_expected']}")
    print(f"  报告：{report_path}")


if __name__ == "__main__":
    main()
