import unittest
from unittest.mock import patch

from agentic.orchestrator import run_agentic_retrieval
from agentic.planner import _constrain_initial_tasks, assess_evidence, plan_retrieval
from agentic.evidence_rules import enforce_evidence_rules
from agentic.schemas import (
    CalculationSpec,
    AnswerSlot,
    Evidence,
    EvidenceAssessment,
    RetrievalPlan,
    RetrievalTask,
)
from agentic.tools import execute_numeric_calculation, execute_retrieval_task
from pipeline.retrieve import retrieve_document_sections


def _task(task_id="T1", query="低压电缆过载判断依据", lane="regulation"):
    return RetrievalTask(
        task_id=task_id,
        goal="查找判断依据",
        query=query,
        lane=lane,
    )


def _knowledge_hit(source="标准.md", chunk_idx=1):
    return {
        "content": "最大相电流应与电缆允许载流量比较。",
        "source": source,
        "doc_type": "regulation",
        "H1": "过载判断",
        "chunk_idx": chunk_idx,
        "distance": 0.2,
        "evidence_type": "knowledge",
    }


class AgenticOrchestratorTests(unittest.TestCase):
    @patch("agentic.planner.complete_json")
    def test_answer_slot_assessment_prompt_is_opt_in(self, complete_json):
        complete_json.return_value = {"sufficient": False}
        assess_evidence("测试问题", [], [], include_answer_slots=False)
        self.assertNotIn("answer_slots", complete_json.call_args.args[0])
        assess_evidence("测试问题", [], [], include_answer_slots=True)
        self.assertIn("answer_slots", complete_json.call_args.args[0])

    def test_governance_question_without_standard_name_uses_all_knowledge(self):
        task = _task(lane="regulation")

        routed = _constrain_initial_tasks(
            "台区频繁停电治理中，线路隐患多少天完成治理？", [task],
        )
        standard = _constrain_initial_tasks(
            "台区频繁停电治理标准有哪些要求？", [task],
        )

        self.assertEqual("all", routed[0].lane)
        self.assertEqual("regulation", standard[0].lane)

    def test_only_existing_direct_evidence_ids_reach_result(self):
        plan = RetrievalPlan(tasks=[_task()])
        result = run_agentic_retrieval(
            "低压电缆过载如何判断？",
            planner=lambda query: plan,
            executor=lambda task, knowledge_top_k, case_top_k: [_knowledge_hit()],
            assessor=lambda query, tasks, evidence: EvidenceAssessment(
                sufficient=True,
                reason="E1直接给出了判断依据",
                answer_evidence_ids=["E1", "E99", "E1"],
                answer_slots=[
                    AnswerSlot(question_part="如何判断", evidence_ids=["E1", "E99", "E1"]),
                    AnswerSlot(question_part="无关子项", evidence_ids=["E1"]),
                ],
            ),
        )

        self.assertEqual(["E1"], result.answer_evidence_ids)
        self.assertEqual(
            [AnswerSlot(question_part="如何判断", evidence_ids=["E1"])],
            result.answer_slots,
        )

    def test_follow_up_does_not_search_cases_for_document_fact_question(self):
        plan = RetrievalPlan(known_facts=[], missing_data=[], tasks=[_task()])
        calls = []

        def executor(task, knowledge_top_k, case_top_k):
            calls.append(task.tool)
            return [_knowledge_hit()]

        run_agentic_retrieval(
            "论文中的事故负荷是多少？",
            planner=lambda query: plan,
            executor=executor,
            assessor=lambda query, tasks, evidence: EvidenceAssessment(
                sufficient=False,
                reason="需要补查",
                follow_up_tasks=[RetrievalTask(
                    task_id="F1", goal="查找案例", query="停电案例",
                    tool="search_cases", lane="cases",
                )],
            ),
        )

        self.assertEqual(["search_knowledge"], calls)

    def test_stops_after_sufficient_initial_evidence(self):
        plan = RetrievalPlan(
            known_facts=["最大相电流492.5A"],
            missing_data=[],
            tasks=[_task()],
        )
        calls = []

        def executor(task, knowledge_top_k, case_top_k):
            calls.append(task.query)
            return [_knowledge_hit()]

        def assessor(query, tasks, evidence):
            return EvidenceAssessment(sufficient=True, reason="E1提供了直接依据")

        result = run_agentic_retrieval(
            "为什么判定为低压电缆过载？",
            planner=lambda query: plan,
            executor=executor,
            assessor=assessor,
        )

        self.assertTrue(result.sufficient)
        self.assertEqual(1, result.rounds_completed)
        self.assertEqual(["低压电缆过载判断依据"], calls)
        self.assertEqual("E1", result.evidence[0].evidence_id)
        self.assertEqual("过载判断", result.evidence[0].section)
        self.assertEqual(0.2, result.evidence[0].distance)

    def test_runs_one_targeted_follow_up_when_evidence_is_insufficient(self):
        plan = RetrievalPlan(
            known_facts=[],
            missing_data=["电缆允许载流量"],
            tasks=[_task()],
        )
        follow_up = _task("F1", "240平方毫米电缆允许载流量", "regulation")
        assessments = iter([
            EvidenceAssessment(
                sufficient=False,
                reason="缺少载流量",
                follow_up_tasks=[follow_up],
            ),
            EvidenceAssessment(sufficient=True, reason="E1和E2补查后充分"),
        ])

        def executor(task, knowledge_top_k, case_top_k):
            chunk_idx = 1 if "过载" in task.query else 2
            return [_knowledge_hit(chunk_idx=chunk_idx)]

        result = run_agentic_retrieval(
            "判断电缆是否过载",
            max_follow_up_rounds=1,
            planner=lambda query: plan,
            executor=executor,
            assessor=lambda query, tasks, evidence: next(assessments),
        )

        self.assertTrue(result.sufficient)
        self.assertEqual(2, result.rounds_completed)
        self.assertEqual(2, len(result.evidence))
        self.assertEqual("R2T1", result.trace[1].task.task_id)
        self.assertEqual([], result.missing_data)

    def test_does_not_repeat_the_same_lane_and_query(self):
        repeated = _task("F1")
        plan = RetrievalPlan(known_facts=[], missing_data=[], tasks=[_task()])
        executor_calls = []

        def executor(task, knowledge_top_k, case_top_k):
            executor_calls.append(task.query)
            return [_knowledge_hit()]

        result = run_agentic_retrieval(
            "判断电缆是否过载",
            max_follow_up_rounds=2,
            planner=lambda query: plan,
            executor=executor,
            assessor=lambda query, tasks, evidence: EvidenceAssessment(
                sufficient=False,
                reason="仍需补查",
                follow_up_tasks=[repeated],
            ),
        )

        self.assertFalse(result.sufficient)
        self.assertEqual(1, result.rounds_completed)
        self.assertEqual(1, len(executor_calls))

    def test_deduplicates_evidence_across_tasks_and_can_hide_trace(self):
        plan = RetrievalPlan(
            known_facts=[],
            missing_data=[],
            tasks=[_task("T1"), _task("T2", "电缆过载保护")],
        )

        result = run_agentic_retrieval(
            "判断电缆是否过载",
            include_trace=False,
            planner=lambda query: plan,
            executor=lambda task, knowledge_top_k, case_top_k: [_knowledge_hit()],
            assessor=lambda query, tasks, evidence: EvidenceAssessment(
                sufficient=True,
                reason="E1提供了直接依据",
            ),
        )

        self.assertEqual(1, len(result.evidence))
        self.assertEqual([], result.trace)

    def test_rejects_unbounded_follow_up_rounds(self):
        with self.assertRaises(ValueError):
            run_agentic_retrieval("测试", max_follow_up_rounds=3)

    def test_executes_grounded_numeric_task_as_evidence(self):
        calculation = CalculationSpec(
            operation="compare",
            left_value=492.5,
            right_value=378,
            left_label="最大相电流",
            right_label="允许载流量",
            unit="A",
        )
        plan = RetrievalPlan(
            known_facts=[],
            missing_data=[],
            tasks=[
                RetrievalTask(
                    task_id="T1",
                    goal="比较电流",
                    query="计算过载幅度",
                    tool="calculate",
                    calculation=calculation,
                )
            ],
        )
        result = run_agentic_retrieval(
            "最大相电流492.5A，允许载流量378A",
            planner=lambda query: plan,
            assessor=lambda query, tasks, evidence: EvidenceAssessment(
                sufficient=True,
                reason="E1完成确定性比较",
            ),
        )

        self.assertEqual("calculation", result.evidence[0].evidence_type)
        self.assertIn("超出30.29%", result.evidence[0].content)

    def test_rejects_calculation_with_ungrounded_operand(self):
        plan = RetrievalPlan(
            known_facts=[],
            missing_data=[],
            tasks=[
                RetrievalTask(
                    task_id="T1",
                    goal="比较电流",
                    query="计算过载幅度",
                    tool="calculate",
                    calculation=CalculationSpec(
                        operation="compare",
                        left_value=492.5,
                        right_value=999,
                    ),
                )
            ],
        )
        result = run_agentic_retrieval(
            "最大相电流492.5A，但没有提供限值",
            planner=lambda query: plan,
            assessor=lambda query, tasks, evidence: self.fail("assessor should not run"),
        )

        self.assertEqual([], result.evidence)
        self.assertEqual(0, result.rounds_completed)


class AgenticToolTests(unittest.TestCase):
    def test_compare_calculation_reports_difference_and_exceedance(self):
        result = execute_numeric_calculation(CalculationSpec(
            operation="compare",
            left_value=492.5,
            right_value=378,
            left_label="最大相电流",
            right_label="允许载流量",
            unit="A",
        ))

        self.assertIn("差值为114.5A", result["content"])
        self.assertIn("超出30.29%", result["content"])
        self.assertAlmostEqual(30.291, result["calculation"]["relative_percent"], 3)

    def test_ratio_calculation_rejects_zero_denominator(self):
        with self.assertRaises(ValueError):
            execute_numeric_calculation(CalculationSpec(
                operation="ratio",
                left_value=10,
                right_value=0,
            ))

    def test_ratio_calculation_reports_excess_above_baseline(self):
        result = execute_numeric_calculation(CalculationSpec(
            operation="ratio",
            left_value=492.5,
            right_value=378,
            left_label="最大相电流",
            right_label="允许载流量",
            unit="A",
        ))

        self.assertIn("= 130.29%", result["content"])
        self.assertIn("超出30.29%", result["content"])
        self.assertAlmostEqual(30.291, result["calculation"]["excess_percent"], 3)

    @patch("agentic.tools.retrieve_document_sections")
    def test_document_task_uses_exact_source(self, scoped_retrieve):
        scoped_retrieve.return_value = [_knowledge_hit("目标规程.md", 8)]
        task = RetrievalTask(
            task_id="T1",
            goal="补查具体条款",
            query="允许载流量是多少",
            tool="search_document",
            source="目标规程.md",
        )

        hits = execute_retrieval_task(task, knowledge_top_k=3)

        scoped_retrieve.assert_called_once_with(
            "允许载流量是多少",
            source="目标规程.md",
            top_k=3,
        )
        self.assertEqual("knowledge", hits[0]["evidence_type"])

    @patch("agentic.tools.retrieve_document_sections")
    def test_document_task_exposes_full_clause_to_assessor(self, scoped_retrieve):
        scoped_retrieve.return_value = [{
            **_knowledge_hit("标准.md", 20),
            "content": "摘要只列出一级用户",
            "body_content": "a)特级用户应具备三路电源。b)一级用户应具备双电源。",
            "context_format": "structured",
        }]
        task = RetrievalTask(
            task_id="T1", goal="补查条款", query="特级用户几路电源",
            tool="search_document", source="标准.md",
        )

        hits = execute_retrieval_task(task)

        self.assertIn("三路电源", hits[0]["content"])
        self.assertNotIn("context_format", hits[0])

    @patch("agentic.tools.retrieve")
    def test_professional_search_reserves_journal_and_thesis_slots(self, retrieve):
        journals = [
            {
                **_knowledge_hit("期刊A.md" if i < 4 else "目标期刊.md", i),
                "rerank_score": 1 - i / 100,
            }
            for i in range(1, 5)
        ]
        theses = [
            {**_knowledge_hit(f"论文{i}.md", i), "rerank_score": 2 - i / 100}
            for i in range(1, 5)
        ]
        retrieve.side_effect = lambda query, top_k, doc_type: (
            journals if doc_type == "journal" else theses
        )
        task = RetrievalTask(
            task_id="T1",
            goal="查找故障机理",
            query="光伏过电压机理",
            tool="search_knowledge",
            lane="professional",
        )

        hits = execute_retrieval_task(task, knowledge_top_k=4)

        self.assertEqual(3, sum(not hit["source"].startswith("论文") for hit in hits))
        self.assertEqual(1, sum(hit["source"].startswith("论文") for hit in hits))
        self.assertIn("目标期刊.md", {hit["source"] for hit in hits})

    def test_document_task_requires_source(self):
        with self.assertRaises(ValueError):
            RetrievalTask(
                task_id="T1",
                goal="补查条款",
                query="允许载流量是多少",
                tool="search_document",
            )

    @patch("pipeline.retrieve._with_structured_context")
    @patch("pipeline.retrieve._hierarchical_select")
    @patch("pipeline.retrieve.rerank")
    @patch("pipeline.retrieve._source_dense_candidates")
    @patch("pipeline.retrieve._source_sparse_candidates")
    def test_document_retrieval_combines_and_reranks_scoped_candidates(
        self,
        sparse_candidates,
        dense_candidates,
        rerank,
        hierarchical_select,
        structured_context,
    ):
        sparse_candidates.return_value = [_knowledge_hit("目标规程.md", 1)]
        dense_candidates.return_value = [
            _knowledge_hit("目标规程.md", 1),
            _knowledge_hit("目标规程.md", 2),
        ]
        rerank.side_effect = lambda query, chunks, **kwargs: chunks
        hierarchical_select.side_effect = lambda chunks, top_k, **kwargs: chunks[:top_k]
        structured_context.side_effect = lambda chunks, query: chunks

        results = retrieve_document_sections(
            "允许载流量是多少",
            source="目标规程.md",
            top_k=2,
        )

        self.assertEqual([1, 2], [item["chunk_idx"] for item in results])
        sparse_candidates.assert_called_once()
        self.assertEqual(["目标规程.md"], sparse_candidates.call_args.args[1])
        self.assertFalse(
            dense_candidates.call_args.kwargs["require_multiple_subqueries"]
        )
        rerank.assert_called_once()


class AgenticPlannerTests(unittest.TestCase):
    @patch("agentic.planner.complete_json")
    def test_non_exact_document_description_uses_general_search(self, complete_json):
        complete_json.return_value = {
            "known_facts": [], "missing_data": [],
            "tasks": [{
                "task_id": "raw-1", "goal": "查找硕士论文",
                "query": "线损异常 灰色关联度",
                "tool": "search_document", "source": "陈思远硕士论文",
            }],
        }
        question = "陈思远的硕士论文中，线损异常采用哪些因素分析？"

        plan = plan_retrieval(question)

        self.assertEqual("search_knowledge", plan.tasks[0].tool)
        self.assertEqual("all", plan.tasks[0].lane)
        self.assertEqual(question, plan.tasks[0].query)

    @patch("agentic.planner.complete_json")
    def test_exact_filename_keeps_document_search(self, complete_json):
        complete_json.return_value = {
            "known_facts": [], "missing_data": [],
            "tasks": [{
                "task_id": "raw-1", "goal": "查指定文件",
                "query": "线损异常 灰色关联度",
                "tool": "search_document", "source": "目标论文.md",
            }],
        }

        plan = plan_retrieval("请仅在目标论文.md中查找灰色关联度方法。")

        self.assertEqual("search_document", plan.tasks[0].tool)

    @patch("agentic.planner.complete_json")
    def test_single_all_lane_search_keeps_original_question(self, complete_json):
        complete_json.return_value = {
            "known_facts": [],
            "missing_data": [],
            "tasks": [{
                "task_id": "raw-1",
                "goal": "查找判断依据",
                "query": "改写后的宽泛问法",
                "tool": "search_knowledge",
                "lane": "all",
            }],
        }
        question = "某台区电缆最大相电流超限，依据是什么？"

        plan = plan_retrieval(question)

        self.assertEqual(question, plan.tasks[0].query)

    @patch("agentic.planner.complete_json")
    def test_paper_case_stays_in_knowledge_search(self, complete_json):
        complete_json.return_value = {
            "known_facts": [],
            "missing_data": [],
            "tasks": [
                {"task_id": "raw-1", "goal": "查案例", "query": "配变烧损",
                 "tool": "search_cases", "lane": "cases"},
                {"task_id": "raw-2", "goal": "查论文", "query": "配变烧损",
                 "tool": "search_knowledge", "lane": "professional"},
            ],
        }
        question = "宫瑞邦等在配变烧损故障案例中记录的最大相电流是多少？"

        plan = plan_retrieval(question)

        self.assertEqual(["search_knowledge"], [task.tool for task in plan.tasks])
        self.assertEqual(question, plan.tasks[0].query)

    @patch("agentic.planner.complete_json")
    def test_multi_task_query_stays_within_schema_limit(self, complete_json):
        complete_json.return_value = {
            "known_facts": [], "missing_data": [],
            "tasks": [
                {"task_id": "raw-1", "goal": "检索标准", "query": "短查询",
                 "tool": "search_knowledge", "lane": "regulation"},
                {"task_id": "raw-2", "goal": "检索案例", "query": "案例",
                 "tool": "search_cases", "lane": "cases"},
            ],
        }
        question = "查找标准与相似案例。" + "电缆" * 240

        plan = plan_retrieval(question)

        self.assertTrue(all(len(task.query) <= 500 for task in plan.tasks))

    @patch("agentic.planner.complete_json")
    def test_normalizes_null_lane_for_non_search_tools(self, complete_json):
        complete_json.return_value = {
            "known_facts": ["最大相电流492.5A"],
            "missing_data": [],
            "tasks": [
                {
                    "task_id": "raw-1",
                    "goal": "在指定文档内查找依据",
                    "query": "低压电缆过载判断依据",
                    "tool": "search_document",
                    "lane": None,
                    "source": "目标论文.md",
                    "calculation": None,
                },
                {
                    "task_id": "raw-2",
                    "goal": "计算超限幅度",
                    "query": "比较492.5A和378A",
                    "tool": "calculate",
                    "lane": None,
                    "source": None,
                    "calculation": {
                        "operation": "compare",
                        "left_value": 492.5,
                        "right_value": 378,
                        "left_label": "最大相电流",
                        "right_label": "允许载流量",
                        "unit": "A",
                    },
                },
            ],
        }

        plan = plan_retrieval("测试问题")

        self.assertEqual(["T1", "T2"], [task.task_id for task in plan.tasks])
        self.assertEqual(["all", "all"], [task.lane for task in plan.tasks])

    @patch("agentic.planner.complete_json")
    def test_normalizes_common_follow_up_aliases(self, complete_json):
        complete_json.return_value = {
            "sufficient": False,
            "reason": "还需要案例",
            "missing_data": [],
            "conflicts": [],
            "follow_up_tasks": [
                {
                    "task_id": "raw-1",
                    "goal": "检索案例",
                    "query": "相似停电案例",
                    "tool": "case",
                    "lane": "case",
                }
            ],
        }

        assessment = assess_evidence("测试问题", [], [])

        self.assertEqual("search_cases", assessment.follow_up_tasks[0].tool)
        self.assertEqual("cases", assessment.follow_up_tasks[0].lane)

    @patch("agentic.planner.complete_json")
    def test_retries_malformed_follow_up_calculation(self, complete_json):
        invalid = {
            "sufficient": False,
            "reason": "需要计算",
            "follow_up_tasks": [{
                "task_id": "F1",
                "goal": "计算改善幅度",
                "query": "计算改善幅度",
                "tool": "calculate",
                "calculation": "(6785-6674)/6674*100",
            }],
        }
        valid = {
            **invalid,
            "follow_up_tasks": [{
                **invalid["follow_up_tasks"][0],
                "calculation": {
                    "operation": "subtract",
                    "left_value": 6785,
                    "right_value": 6674,
                },
            }],
        }
        complete_json.side_effect = [invalid, valid]

        assessment = assess_evidence("比较两种方案", [], [])

        self.assertEqual(2, complete_json.call_count)
        self.assertEqual(6785, assessment.follow_up_tasks[0].calculation.left_value)
        self.assertEqual("compare", assessment.follow_up_tasks[0].calculation.operation)

    @patch("agentic.planner.complete_json")
    def test_drops_malformed_calculation_after_repair_attempt(self, complete_json):
        invalid = {
            "sufficient": True,
            "reason": "需要计算",
            "follow_up_tasks": [{
                "task_id": "F1",
                "goal": "计算改善幅度",
                "query": "计算改善幅度",
                "tool": "calculate",
                "calculation": "(6785-6674)/6674*100",
            }],
        }
        complete_json.side_effect = [invalid, invalid]

        assessment = assess_evidence("比较两种方案", [], [])

        self.assertFalse(assessment.sufficient)
        self.assertEqual([], assessment.follow_up_tasks)
        self.assertIn("未执行", assessment.reason)

    @patch("agentic.planner.complete_json")
    def test_case_only_request_prunes_unneeded_knowledge_tasks(self, complete_json):
        complete_json.return_value = {
            "known_facts": [],
            "missing_data": [],
            "tasks": [
                {
                    "task_id": "raw-1",
                    "goal": "查找案例",
                    "query": "配变过负荷案例",
                    "tool": "search_cases",
                    "lane": "cases",
                },
                {
                    "task_id": "raw-2",
                    "goal": "查找机理",
                    "query": "配变过负荷机理",
                    "tool": "search_knowledge",
                    "lane": "professional",
                },
            ],
        }
        query = "请用已确认案例证明某台区停电是配变过负荷。"

        plan = plan_retrieval(query)

        self.assertEqual(["search_cases"], [task.tool for task in plan.tasks])
        self.assertIn(query, plan.tasks[0].query)

    @patch("agentic.planner.complete_json")
    def test_case_comparison_keeps_knowledge_and_case_tools(self, complete_json):
        complete_json.return_value = {
            "known_facts": [],
            "missing_data": [],
            "tasks": [
                {
                    "task_id": "raw-1",
                    "goal": "查找案例",
                    "query": "配变过负荷案例",
                    "tool": "search_cases",
                    "lane": "cases",
                },
                {
                    "task_id": "raw-2",
                    "goal": "查找判断原则",
                    "query": "案例适用条件",
                    "tool": "search_knowledge",
                    "lane": "professional",
                },
            ],
        }

        plan = plan_retrieval(
            "相似案例与当前事件特征不同，能否直接沿用案例原因？"
        )

        self.assertEqual(
            {"search_cases", "search_knowledge"},
            {task.tool for task in plan.tasks},
        )

    @patch("agentic.planner.complete_json")
    def test_protection_validation_prunes_case_search(self, complete_json):
        complete_json.return_value = {
            "known_facts": [],
            "missing_data": [],
            "tasks": [
                {
                    "task_id": "raw-1",
                    "goal": "查找规程",
                    "query": "保护整定要求",
                    "tool": "search_knowledge",
                    "lane": "regulation",
                },
                {
                    "task_id": "raw-2",
                    "goal": "查找案例",
                    "query": "保护动作案例",
                    "tool": "search_cases",
                    "lane": "cases",
                },
            ],
        }

        plan = plan_retrieval("没有整定值和动作时间，能否确认保护动作正确？")

        self.assertEqual(["search_knowledge"], [task.tool for task in plan.tasks])


class EvidenceRuleTests(unittest.TestCase):
    def _evidence(self, **updates):
        values = {
            "evidence_id": "E1",
            "task_id": "T1",
            "evidence_type": "knowledge",
            "source": "论文.md",
            "content": "故障分析证据",
            "doc_type": "journal",
        }
        values.update(updates)
        return Evidence(**values)

    def test_missing_citation_warns_without_overriding_sufficiency(self):
        corrected, coverage, issues = enforce_evidence_rules(
            "分析故障原因",
            [_task(lane="professional")],
            [self._evidence()],
            EvidenceAssessment(sufficient=True, reason="证据充分"),
        )

        self.assertTrue(corrected.sufficient)
        self.assertTrue(coverage[0].satisfied)
        self.assertIn("未引用任何 evidence_id", issues[0])

    def test_standard_question_requires_regulation_evidence(self):
        corrected, coverage, _ = enforce_evidence_rules(
            "标准条款对低压电缆载流量如何规定？",
            [_task()],
            [self._evidence()],
            EvidenceAssessment(sufficient=True, reason="E1给出了依据"),
        )

        self.assertFalse(corrected.sufficient)
        requirement = next(item for item in coverage if item.requirement == "regulation")
        self.assertFalse(requirement.satisfied)
        self.assertEqual("regulation", corrected.follow_up_tasks[0].lane)

    def test_verified_case_requirement_rejects_unverified_case(self):
        case = self._evidence(
            evidence_type="case",
            source="案例库.docx",
            doc_type=None,
            case_id="CASE-1",
            verification_status="待专家确认",
        )
        corrected, coverage, _ = enforce_evidence_rules(
            "是否有已确认案例支持这一判断？",
            [RetrievalTask(
                task_id="T1",
                goal="检索案例",
                query="类似停电案例",
                tool="search_cases",
                lane="cases",
            )],
            [case],
            EvidenceAssessment(sufficient=True, reason="E1是案例证据"),
        )

        self.assertFalse(corrected.sufficient)
        verified = next(
            item for item in coverage if item.requirement == "verified_case"
        )
        self.assertFalse(verified.satisfied)

    def test_unsupported_coverage_claim_is_exposed(self):
        corrected, _, issues = enforce_evidence_rules(
            "分析故障原因",
            [_task(lane="professional")],
            [self._evidence()],
            EvidenceAssessment(
                sufficient=True,
                reason="E1表明标准规程依据和案例参考均已覆盖充分",
            ),
        )

        self.assertTrue(corrected.sufficient)
        self.assertEqual(2, len(issues))
        self.assertIn("校验提示", corrected.reason)

    def test_calculation_only_question_drops_unrelated_missing_data(self):
        task = RetrievalTask(
            task_id="T1",
            goal="计算比例",
            query="计算492.5与378的比例",
            tool="calculate",
            calculation=CalculationSpec(
                operation="ratio",
                left_value=492.5,
                right_value=378,
            ),
        )
        calculation = self._evidence(
            evidence_type="calculation",
            source="确定性数值计算",
            doc_type="calculation",
            calculation={"ratio_percent": 130.29},
        )
        corrected, _, _ = enforce_evidence_rules(
            "最大相电流492.5A，允许载流量378A，请计算超限幅度。",
            [task],
            [calculation],
            EvidenceAssessment(
                sufficient=True,
                reason="E1给出计算结果",
                missing_data=["现场测温数据"],
            ),
        )

        self.assertTrue(corrected.sufficient)
        self.assertEqual([], corrected.missing_data)

    def test_lookup_request_does_not_override_assessor_insufficiency(self):
        task = RetrievalTask(
            task_id="T1",
            goal="查找机理",
            query="电缆受潮故障机理",
            tool="search_knowledge",
            lane="professional",
        )
        corrected, coverage, _ = enforce_evidence_rules(
            "请查找低压电缆受潮故障机理。",
            [task],
            [self._evidence()],
            EvidenceAssessment(
                sufficient=False,
                reason="还缺少现场绝缘检测结果",
                missing_data=["现场绝缘检测结果"],
            ),
        )

        self.assertFalse(corrected.sufficient)
        self.assertEqual([], corrected.missing_data)
        self.assertEqual([], corrected.follow_up_tasks)
        self.assertTrue(all(item.satisfied for item in coverage))

    def test_declared_business_gap_stops_document_follow_up(self):
        follow_up = RetrievalTask(
            task_id="F1",
            goal="继续查找",
            query="低压电缆过载",
            tool="search_document",
            source="标准.md",
        )
        corrected, _, _ = enforce_evidence_rules(
            "仅知道负载率70%，没有三相电流和电缆型号，能否排除过载？",
            [_task()],
            [self._evidence(doc_type="regulation")],
            EvidenceAssessment(
                sufficient=True,
                reason="E1给出了判断原则",
                follow_up_tasks=[follow_up],
            ),
        )

        self.assertFalse(corrected.sufficient)
        self.assertEqual([], corrected.follow_up_tasks)
        self.assertIn("三相电流", corrected.missing_data[0])
        self.assertIn("电缆型号", corrected.missing_data[0])


if __name__ == "__main__":
    unittest.main()
