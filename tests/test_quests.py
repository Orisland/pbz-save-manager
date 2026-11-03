import json
import unittest
from pathlib import Path

from pbz_save_manager.quests import describe_narrative, parse_saved_quests
from pbz_save_manager.summary import view_slot

DECODED = Path(r"G:\Project\新建文件夹 (2)\decoded\AutoSave0.json")


def _branch(branch_id: str, branch_type: str, progress: list[int] | None = None, dirty: str = "0001.01.01-00.00.00") -> dict:
    item = {"BranchID": branch_id, "BranchType": branch_type, "DirtyTime": dirty}
    if progress is not None:
        item["TasksProgress"] = progress
    return item


def _quest(name: str, completion: str, branches: list[dict], completed: list[str] | None = None) -> dict:
    item = {
        "QuestClass": f"/Game/PBZstory/PBZQuest/Simple/{name}.{name}_C",
        "QuestBranches": branches,
        "QuestCompletion": completion,
        "ReachedStateNames": [],
    }
    if completed is not None:
        item["CompletedBranches"] = completed
    return item


class QuestReadTests(unittest.TestCase):
    def test_partial_progress_names_unfinished_steps(self) -> None:
        narrative = {
            "TrackedQuestPath": "/Game/PBZstory/PBZQuest/Simple/XiongFu.XiongFu_C",
            "CurrentMainQuestPath": "",
            "CurrentTutorialQuestPath": "",
            "LastUpdatedMainQuestPath": "",
            "NarrativeSavedQuests": [
                _quest(
                    "XiongFu",
                    "EQuestCompletion::QC_Started",
                    [
                        _branch("1", "EBranchType::BranchActivating", [1, 1, 0, 1, 1, 1, 1], "2026.10.29-14.55.29"),
                        _branch("2", "EBranchType::None"),
                    ],
                )
            ],
        }
        text = describe_narrative(narrative)
        self.assertIn("追踪任务：简单 XiongFu（进行中）", text)
        self.assertIn("简单 XiongFu  正在追踪", text)
        self.assertIn("分支 1  进行中  子任务 6/7，未完成第 3 项  2026.10.29-14.55.29", text)
        self.assertIn("未开始分支：2", text)
        self.assertNotIn("子任务 0/", text)

    def test_completed_branch_hides_cleared_counters(self) -> None:
        narrative = {
            "NarrativeSavedQuests": [
                _quest(
                    "DoneQuest",
                    "EQuestCompletion::QC_Succeded",
                    [_branch("1", "EBranchType::BranchCompleted", [0, 0], "2026.10.29-12.26.27")],
                    ["1"],
                )
            ]
        }
        text = describe_narrative(narrative)
        self.assertIn("【已完成】", text)
        self.assertIn("简单 DoneQuest  分支 1  2026.10.29-12.26.27", text)
        self.assertNotIn("子任务", text)

    def test_failed_locked_branch_keeps_zero_progress(self) -> None:
        narrative = {
            "NarrativeSavedQuests": [
                _quest(
                    "JiuSiFuShang",
                    "EQuestCompletion::QC_Failed",
                    [_branch("1", "EBranchType::BranchLocked", [0, 0, 0, 0], "2026.10.30-00.22.06")],
                )
            ]
        }
        text = describe_narrative(narrative)
        self.assertIn("【已失败】", text)
        self.assertIn("分支 1  锁定  子任务 0/4  2026.10.30-00.22.06", text)

    def test_active_branch_without_counters_does_not_invent_a_fraction(self) -> None:
        narrative = {
            "NarrativeSavedQuests": [
                _quest(
                    "MainCh2-SiDaSheWu",
                    "EQuestCompletion::QC_Started",
                    [_branch("S1", "EBranchType::BranchActivating", None, "2026.10.30-00.33.06")],
                )
            ]
        }
        narrative["NarrativeSavedQuests"][0]["QuestClass"] = (
            "/Game/PBZstory/PBZQuest/Main/MainCh2-SiDaSheWu.MainCh2-SiDaSheWu_C"
        )
        text = describe_narrative(narrative)
        self.assertIn("分支 S1  进行中  2026.10.30-00.33.06", text)
        self.assertNotIn("子任务", text)

    def test_numeric_counters_stay_raw_when_they_are_not_flags(self) -> None:
        narrative = {
            "NarrativeSavedQuests": [
                _quest(
                    "Counted",
                    "EQuestCompletion::QC_Started",
                    [_branch("1", "EBranchType::BranchActivating", [2, 0, 5])],
                )
            ]
        }
        records = parse_saved_quests(narrative)
        self.assertEqual(records[0].branches[0].progress, (2, 0, 5))
        self.assertIn("子任务计数 2、0、5", describe_narrative(narrative))

    def test_gameplay_view_includes_quest_progress(self) -> None:
        payload = {
            "PBZGameSystem": {},
            "Player": {"Dust": 1, "RemainingDays": 2},
            "Narrative": {
                "NarrativeSavedQuests": [
                    _quest(
                        "XiongFu",
                        "EQuestCompletion::QC_Started",
                        [_branch("1", "EBranchType::BranchActivating", [1, 0])],
                    )
                ]
            },
        }
        detail = view_slot("AutoSave0", payload).detail
        self.assertIn("尘埃：1", detail)
        self.assertIn("子任务 1/2，未完成第 2 项", detail)

    def test_sample_save_groups_progress_and_completion(self) -> None:
        if not DECODED.is_file():
            self.skipTest("没有已解开的样例")
        narrative = json.loads(DECODED.read_text(encoding="utf-8"))["Narrative"]
        text = describe_narrative(narrative)
        self.assertIn("追踪任务：主线 MainCh2-SiDaSheWu（进行中）", text)
        self.assertIn("主线 MainCh2-SiDaSheWu  正在追踪", text)
        self.assertIn("分支 S1  进行中  2026.10.30-00.33.06", text)
        self.assertIn("简单 XiongFu", text)
        self.assertIn("子任务 6/7，未完成第 3 项", text)
        self.assertIn("收集 JiaoTuSanKu", text)
        self.assertIn("子任务 1/5，未完成第 2、3、4、5 项", text)
        self.assertIn("【已失败】", text)
        self.assertIn("简单 JiuSiFuShang", text)
        self.assertIn("分支 1  锁定  子任务 0/4", text)
        self.assertIn("教学 Tutorial_ZLGW_Attack  分支 1、2  2026.10.29-12.26.27", text)
        self.assertNotIn("分支 S1  进行中  子任务", text)
