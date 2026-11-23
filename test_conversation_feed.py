import tkinter as tk
import unittest

import desktop_style
from conversation_feed import ConversationFeed


class ConversationFeedTests(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.addCleanup(self.root.destroy)
        desktop_style.configure(self.root)
        self.root.geometry("270x220")
        self.feed = ConversationFeed(self.root)
        self.feed.pack(fill="both", expand=True)
        self.root.update()

    def tagged(self, name):
        log = self.feed.log
        ranges = log.tag_ranges(name)
        return [log.get(start, end) for start, end in zip(ranges[::2], ranges[1::2])]

    def test_history_keeps_recipients_and_continuation_lines(self):
        self.feed.append("工作台 → Codex [派单 · 等待软件领取] 等待中。\n"
                         "Codex → Claude Desktop, ZCode [任务交接] 核对交付。\n保留第二行。\n"
                         "集成文件 report.md\nCodex [验收清单] 通过。")
        self.assertEqual([s.strip() for s in self.tagged("speaker")], ["工作台", "Codex", "Codex"])
        self.assertTrue(any("Claude Desktop, ZCode" in value for value in self.tagged("to")))
        self.assertTrue(any("核对交付。\n保留第二行。" in value for value in self.tagged("message")))
        self.assertTrue(any("任务交接" in value for value in self.tagged("handoff")))
        self.assertIn("集成文件 report.md\n", self.tagged("system"))
        self.assertEqual(self.feed.log.cget("state"), "disabled")
        self.feed.clear()
        self.assertFalse(self.tagged("speaker"))
        self.assertEqual(self.feed.empty_feed.winfo_manager(), "place")

    def test_scrolling_back_keeps_reading_position_and_latest_button_catches_up(self):
        for index in range(36):
            self.feed.append(f"Codex → Claude Desktop [派单] 任务 {index}")
        self.root.update()
        log = self.feed.log
        log.yview_moveto(.2)
        self.root.update()
        previous = log.index("@0,0")
        self.feed.append("Claude Desktop → Codex [实现] 最新交付")
        self.root.update()
        self.assertEqual(log.index("@0,0"), previous)
        self.assertTrue(self.feed.latest.winfo_ismapped())
        self.feed.latest.invoke()
        self.root.update()
        self.assertAlmostEqual(log.yview()[1], 1.0, places=2)
        self.assertFalse(self.feed.latest.winfo_ismapped())
