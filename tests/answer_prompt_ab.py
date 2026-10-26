#!/usr/bin/env python3
"""Compare a focused answer prompt against cached answers on fixed evidence."""

import argparse
import asyncio
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from openai import AsyncOpenAI, OpenAI  # noqa: E402
from ragas.evaluation import llm_factory  # noqa: E402
from ragas.metrics.collections import (  # noqa: E402
    AnswerCorrectness,
    AnswerRelevancy,
    Faithfulness,
)

from config import DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL, DEEPSEEK_MODEL  # noqa: E402
from pipeline.generate import (  # noqa: E402
    _format_context, _REVIEW_PROMPT, _ANCHOR_REVIEW_PROMPT,
    _SLOT_GUIDANCE, rag_answer, review_answer,
    usable_answer_slots,
)
from agentic.planner import assess_evidence  # noqa: E402
from tests.agentic_unified_eval import _save  # noqa: E402
from tests.ragas_eval import _build_evaluator_embeddings  # noqa: E402

CACHE_FILE = ROOT / "tests" / "agentic_unified_cache.json"
BASELINE_FILE = ROOT / "tests" / "agentic_unified_ragas_scores.json"
ANSWER_FILE = ROOT / "tests" / "answer_prompt_ab_answers.json"
SCORE_FILE = ROOT / "tests" / "answer_prompt_ab_scores.json"
METRICS = ("faithfulness", "answer_relevancy", "answer_correctness")

FOCUSED_PROMPT = """你是400V低压配电网的专业问答助手。只依据下方参考片段回答，不得编造事实、数值或来源。

回答规则：
1. 按问题顺序逐项回答，先写直接结论，再写必要条件；数值保留单位和对应对象。
2. 若问题给出的条件满足片段中的一般条件，可以直接应用该条件得出结论，不必要求原文重复问题中的每个数字；不得推断片段未支持的细节。
3. 若片段只适用于相近而非完全相同的场景，明确写出适用前提，再给出该场景下的数值或做法，不得把它说成普遍规定。
4. 只有某一子项缺乏证据时，只对该项简短说明证据不足；已能回答的子项必须直接回答。
5. 不复述问题，不添加未问的背景、推测或重复解释。来源最多在末尾用一句话说明，不逐条重复引用片段编号。

知识库参考片段：
{context}"""

REVISION_PROMPT = _REVIEW_PROMPT

ANCHORED_REVIEW_PROMPT = _ANCHOR_REVIEW_PROMPT


def _fingerprint(question: str, hits: list[dict], prompt: str, draft: str = "") -> str:
    payload = json.dumps(
        [DEEPSEEK_MODEL, prompt, question, draft, _format_context(hits)],
        ensure_ascii=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _average(rows: list[dict]) -> dict:
    return {
        metric: round(sum(row[metric] for row in rows) / len(rows), 4)
        for metric in METRICS
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ids", nargs="+")
    parser.add_argument("--phase", choices=("generate", "score", "all"), default="all")
    parser.add_argument("--variant", choices=("focused", "revision", "slots", "anchored"), default="focused")
    args = parser.parse_args()

    prompt = {"focused": FOCUSED_PROMPT, "revision": REVISION_PROMPT,
              "slots": _SLOT_GUIDANCE, "anchored": ANCHORED_REVIEW_PROMPT}[args.variant]
    answer_file = {"focused": ANSWER_FILE, "revision": ROOT / "tests" / "answer_revision_ab_answers.json",
                   "slots": ROOT / "tests" / "answer_slots_ab_answers.json",
                   "anchored": ROOT / "tests" / "answer_anchored_ab_answers.json"}[args.variant]
    score_file = {"focused": SCORE_FILE, "revision": ROOT / "tests" / "answer_revision_ab_scores.json",
                  "slots": ROOT / "tests" / "answer_slots_ab_scores.json",
                  "anchored": ROOT / "tests" / "answer_anchored_ab_scores.json"}[args.variant]

    cases = {row["id"]: row for row in json.loads(
        (ROOT / "tests" / "test_cases_with_ref.json").read_text(encoding="utf-8")
    )}
    retrieval = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
    answers = json.loads(answer_file.read_text(encoding="utf-8")) if answer_file.exists() else {}
    scores = json.loads(score_file.read_text(encoding="utf-8")) if score_file.exists() else {}
    baseline = json.loads(BASELINE_FILE.read_text(encoding="utf-8"))
    selected = list(dict.fromkeys(args.ids or cases.keys()))
    missing = [item_id for item_id in selected if item_id not in cases or item_id not in retrieval]
    if missing:
        parser.error("题目缺失：" + ", ".join(missing))

    if args.phase in {"generate", "all"}:
        client = OpenAI(api_key=DEEPSEEK_API_KEY, base_url=DEEPSEEK_BASE_URL)
        for index, item_id in enumerate(selected, 1):
            row = retrieval[item_id]
            draft = row["answer"] if args.variant in {"revision", "anchored"} else ""
            source_version = (
                hashlib.sha256(b"".join(path.read_bytes() for path in (
                    ROOT / "agentic" / "planner.py",
                    ROOT / "pipeline" / "generate.py",
                ))).hexdigest() if args.variant == "slots" else ""
            )
            fingerprint = _fingerprint(cases[item_id]["question"], row["hits"], prompt + source_version, draft)
            if answers.get(item_id, {}).get("fingerprint") == fingerprint:
                continue
            print(f"[{index}/{len(selected)}] {item_id} generate", flush=True)
            if args.variant == "slots":
                result = row["result"]
                tasks = [step["task"] for step in result.get("trace", [])]
                assessment = assess_evidence(
                    cases[item_id]["question"], tasks, result["evidence"],
                    include_answer_slots=True,
                )
                known_ids = {hit["evidence_id"] for hit in result["evidence"]}
                slots = [
                    {"question_part": slot.question_part,
                     "evidence_ids": [item_id for item_id in dict.fromkeys(slot.evidence_ids)
                                      if item_id in known_ids]}
                    for slot in assessment.answer_slots
                    if "".join(slot.question_part.split()) in "".join(cases[item_id]["question"].split())
                ]
                slots = usable_answer_slots(row["hits"], slots)
                if slots:
                    response_text = rag_answer(
                        cases[item_id]["question"], row["hits"], answer_slots=slots,
                    )
                    response_text = review_answer(
                        cases[item_id]["question"], row["hits"], response_text,
                        answer_slots=slots,
                    )
                else:
                    response_text = row["answer"]
            else:
                response = client.chat.completions.create(
                    model=DEEPSEEK_MODEL,
                    messages=[
                        {"role": "system", "content": prompt.format(
                            context=_format_context(row["hits"]),
                        )},
                        {"role": "user", "content": (
                            json.dumps({"question": cases[item_id]["question"],
                                        "draft": draft}, ensure_ascii=False)
                            if args.variant in {"revision", "anchored"} else cases[item_id]["question"]
                        )},
                    ],
                )
                response_text = response.choices[0].message.content
            answers[item_id] = {
                "fingerprint": fingerprint,
                "response": response_text,
                **({"answer_slots": slots} if args.variant == "slots" else {}),
            }
            _save(answer_file, answers)

    if args.phase in {"score", "all"}:
        embeddings = _build_evaluator_embeddings()
        judge = llm_factory(
            DEEPSEEK_MODEL,
            client=AsyncOpenAI(api_key=DEEPSEEK_API_KEY, base_url=DEEPSEEK_BASE_URL),
            max_tokens=8192,
            extra_body={"thinking": {"type": "disabled"}},
        )
        evaluators = {
            "faithfulness": Faithfulness(llm=judge),
            "answer_relevancy": AnswerRelevancy(llm=judge, embeddings=embeddings),
            "answer_correctness": AnswerCorrectness(llm=judge, embeddings=embeddings),
        }

        async def score_all() -> None:
            for index, item_id in enumerate(selected, 1):
                case = cases[item_id]
                hit_row = retrieval[item_id]
                answer = answers.get(item_id)
                draft = hit_row["answer"] if args.variant in {"revision", "anchored"} else ""
                source_version = (
                    hashlib.sha256(b"".join(path.read_bytes() for path in (
                        ROOT / "agentic" / "planner.py",
                        ROOT / "pipeline" / "generate.py",
                    ))).hexdigest() if args.variant == "slots" else ""
                )
                if not answer or answer.get("fingerprint") != _fingerprint(
                    case["question"], hit_row["hits"], prompt + source_version, draft,
                ):
                    raise RuntimeError(f"{item_id} 缺少当前提示词的答案")
                fingerprint = hashlib.sha256(json.dumps(
                    [case["question"], case["reference"], answer["response"],
                     [hit["content"] for hit in hit_row["hits"]]],
                    ensure_ascii=False,
                ).encode("utf-8")).hexdigest()
                cached = scores.get(item_id, {})
                row_scores = dict(cached.get("scores", {})) if cached.get(
                    "fingerprint",
                ) == fingerprint else {}
                if args.variant in {"revision", "slots", "anchored"} and answer["response"] == hit_row["answer"]:
                    row_scores = {
                        name: baseline[item_id]["scores"][name] for name in METRICS
                    }
                    scores[item_id] = {"fingerprint": fingerprint, "scores": row_scores}
                    _save(score_file, scores)
                missing_metrics = [name for name in METRICS if name not in row_scores]
                if missing_metrics:
                    calls = {
                        "faithfulness": lambda: evaluators["faithfulness"].ascore(
                            case["question"], answer["response"],
                            [hit["content"] for hit in hit_row["hits"]],
                        ),
                        "answer_relevancy": lambda: evaluators["answer_relevancy"].ascore(
                            case["question"], answer["response"],
                        ),
                        "answer_correctness": lambda: evaluators["answer_correctness"].ascore(
                            case["question"], answer["response"], case["reference"],
                        ),
                    }
                    results = await asyncio.gather(
                        *(calls[name]() for name in missing_metrics),
                        return_exceptions=True,
                    )
                    for name, value in zip(missing_metrics, results):
                        if isinstance(value, Exception):
                            raise RuntimeError(f"{item_id}:{name} 评分失败") from value
                        row_scores[name] = float(value.value)
                    scores[item_id] = {"fingerprint": fingerprint, "scores": row_scores}
                    _save(score_file, scores)
                print(f"[{index}/{len(selected)}] {item_id} " + " ".join(
                    f"{name}={row_scores[name]:.3f}" for name in METRICS
                ), flush=True)

        asyncio.run(score_all())
        baseline_rows = [baseline[item_id]["scores"] for item_id in selected]
        candidate_rows = [scores[item_id]["scores"] for item_id in selected]
        report = {
            "questions": len(selected),
            "sample_ids": selected,
            "baseline": _average(baseline_rows),
            args.variant: _average(candidate_rows),
            "changed_answers": sum(
                answers[item_id]["response"] != retrieval[item_id]["answer"]
                for item_id in selected
            ) if args.variant in {"revision", "slots", "anchored"} else None,
            "model": DEEPSEEK_MODEL,
            "triggered": sum(bool(answers[item_id].get("answer_slots")) for item_id in selected)
            if args.variant == "slots" else None,
            "comparison": "Same questions and retrieved contexts; unchanged answers reuse baseline scores.",
        }
        print(json.dumps(report, ensure_ascii=False, indent=2))
        report_file = ROOT / "tests" / "reports" / f"answer_{args.variant}_ab_{len(selected)}.json"
        _save(report_file, report)
        print(f"报告：{report_file}")


if __name__ == "__main__":
    main()
