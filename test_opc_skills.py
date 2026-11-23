import unittest
from pathlib import Path

from opc_skills import LEAD, ROLE_NAMES, role_info


class EmployeeSkillsTests(unittest.TestCase):
    def test_offline_handbooks_match_runtime_content_and_have_versioned_sources(self):
        self.assertEqual(LEAD, "项目经理")
        self.assertEqual(ROLE_NAMES, ("项目经理", "前端设计师", "后端开发员", "测试工程师"))
        root = Path(__file__).with_name("opc_skills")
        roles = dict(zip(ROLE_NAMES, ["project-manager", "frontend-designer", "backend-developer", "qa-engineer"]))
        for role, folder in roles.items():
            with self.subTest(role=role):
                info = role_info(role)
                body = (root / folder / "SKILL.md").read_text("utf-8").split("\n---\n", 1)[1].strip()
                self.assertEqual(info["instructions"], body)
                self.assertIn(role, body)
                self.assertGreater(len(body), 200)
                self.assertEqual(len(info["skills"]), len(info["sources"]))
                for url in info["sources"]:
                    self.assertRegex(url, r"^https://github\.com/[^/]+/[^/]+/blob/[a-f0-9]{40}/skills/")
        for filename in ("LICENSE-mattpocock.txt", "LICENSE-awesome-copilot.txt"):
            self.assertIn("Permission is hereby granted", (root / filename).read_text("utf-8"))
        with self.assertRaises(ValueError):
            role_info("../README")
