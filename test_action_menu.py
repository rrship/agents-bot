import gc
import tkinter as tk
import unittest
from unittest.mock import patch

from action_menu import ActionMenu


def tearDownModule():
    # Tk interpreters must be finalized here, before later suites start workers.
    gc.collect()


class ActionMenuTests(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.addCleanup(self.root.destroy)
        self.calls = []
        self.menu = ActionMenu(self.root, text="任务菜单")
        self.menu.pack()
        self.first = self.menu.add_action("暂停", lambda: self.calls.append("pause"))
        self.menu.add_separator()
        self.middle = self.menu.add_action("继续", lambda: self.calls.append("resume"), enabled=False)
        self.menu.add_action("新建", lambda: self.calls.append("new"))
        self.root.geometry("300x200+80+80")
        self.root.update()
        self.menu.focus_force()
        self.root.update()

    def click(self, widget):
        widget.event_generate("<Enter>", x=5, y=5)
        widget.event_generate("<ButtonPress-1>", x=5, y=5)
        widget.event_generate("<ButtonRelease-1>", x=5, y=5)
        self.root.update()

    def test_keyboard_skips_disabled_and_restores_focus(self):
        self.menu.event_generate("<Key-Down>")
        self.root.update()
        popup = self.menu.popup
        self.assertIsNotNone(popup)
        popup.event_generate("<Key-Down>")
        popup.event_generate("<Key-Return>")
        self.root.update()
        self.assertEqual(self.calls, ["new"])
        self.assertIsNone(self.menu.popup)
        self.assertIs(self.root.focus_get(), self.menu)

    def test_mouse_skips_disabled_and_invokes_active_item(self):
        self.click(self.menu)
        rows = [r for r, _ in self.menu._rows]
        self.click(rows[1])
        self.assertIsNotNone(self.menu.popup)
        self.assertEqual(self.calls, [])
        self.menu.popup.event_generate("<Key-Escape>")
        self.root.update()
        self.click(self.menu)
        self.click(self.menu._rows[0][0])
        self.assertEqual(self.calls, ["pause"])
        self.assertIsNone(self.menu.popup)

    def test_open_menu_reflects_changes_to_available_commands(self):
        self.click(self.menu)
        self.middle.configure(state="normal", text="重新启动")
        self.first.configure(state="disabled")
        rows = [r for r, _ in self.menu._rows]
        self.assertEqual(str(rows[0].cget("state")), "disabled")
        self.assertEqual(rows[1].cget("text"), "重新启动")
        self.click(rows[1])
        self.assertEqual(self.calls, ["resume"])

    def test_cancel_dismisses_and_restores_modal_grab(self):
        modal = tk.Toplevel(self.root)
        button = ActionMenu(modal)
        button.pack()
        button.add_action("运行", lambda: self.calls.append("run"))
        self.root.update()
        modal.grab_set()
        button.focus_force()
        self.root.update()
        for key in ("Escape", "Tab"):
            button.event_generate("<Key-Return>")
            self.root.update()
            popup = button.popup
            self.assertIsNotNone(popup)
            self.assertIs(self.root.grab_current(), popup)
            popup.event_generate("<Key-" + key + ">")
            self.root.update()
            self.assertIsNone(button.popup)
            self.assertIs(self.root.grab_current(), modal)
        self.assertEqual(self.calls, [])

    def test_outside_press_closes_without_running_an_action(self):
        self.click(self.menu)
        self.menu.popup.event_generate("<ButtonPress-1>", x=-4, y=-4)
        self.root.update()
        self.assertIsNone(self.menu.popup)
        self.assertEqual(self.calls, [])

    def test_disabling_open_menu_prevents_execution(self):
        self.menu.event_generate("<Key-Down>")
        self.root.update()
        self.menu.state(["disabled"])
        self.menu.popup.event_generate("<Key-Return>")
        self.root.update()
        self.assertEqual(self.calls, [])

    def test_popup_follows_owner_on_left_hand_monitor(self):
        with patch.object(self.menu, "_work_area", return_value=(-1280, -80, 0, 620), create=True), \
             patch.object(self.menu, "winfo_rootx", return_value=-90), \
             patch.object(self.menu, "winfo_rooty", return_value=591):
            self.menu.open()
        self.root.update()
        popup = self.menu.popup
        self.assertGreaterEqual(popup.winfo_rootx(), -1280)
        self.assertLessEqual(popup.winfo_rootx() + popup.winfo_width(), 0)
        self.assertLessEqual(popup.winfo_rooty() + popup.winfo_height(), 591)

    def test_right_aligned_trigger_keeps_wider_menu_over_owner(self):
        self.menu.pack_forget()
        self.menu.pack(side="right")
        self.root.update()
        self.menu.open()
        self.root.update()
        popup = self.menu.popup
        self.assertLessEqual(popup.winfo_rootx() + popup.winfo_width(),
                             self.root.winfo_rootx() + self.root.winfo_width())
