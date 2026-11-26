"""Run from the repository root: python3 -B -m unittest discover -s ."""

import unittest
from types import SimpleNamespace
from unittest.mock import patch

import bridge_core as core


class Screen:
    Synchronous = False
    IgnoreEscape = False
    MatchIndex = 0

    def __init__(self):
        self.now = 0.0
        self.pending = ""
        self.pages = []
        self.sent = []

    def Send(self, text):
        self.sent.append(text)
        if text == " " and self.pages:
            self.pending += self.pages.pop(0)

    def ReadString(self, matches, timeout):
        first = len(self.pending)
        found = None
        for index, match in enumerate(matches):
            position = self.pending.find(match)
            if 0 <= position < first:
                first = position
                found = index
        if found is None:
            # SecureCRT discards unmatched text on timeout.
            self.pending = ""
            self.MatchIndex = 0
            self.now += timeout + 0.001
            return ""
        text = self.pending[:first]
        self.pending = self.pending[first + len(matches[found]):]
        self.MatchIndex = found + 1
        self.now += 0.00001
        return text


class CaptureTests(unittest.TestCase):
    def setUp(self):
        self.screen = Screen()
        tab = SimpleNamespace(Screen=self.screen, Session=SimpleNamespace(Connected=True))
        self.engine = core.SessionEngine(tab, "a" * 32, "test", lambda: self.screen.now)
        self.job_id = "b" * 32
        self.profile = patch.object(core, "get_command_profile", return_value="IOSXE-readonly")
        self.profile.start()
        self.addCleanup(self.profile.stop)

    def start(self, **options):
        request = {"operation": "run", "job_id": self.job_id, "command": "show version",
                   "expected_prompt": "router#"}
        request.update(options)
        return self.engine._start(request)

    def result(self, **options):
        return self.engine.output(self.job_id, **options)

    def test_automatic_paging_in_both_modes(self):
        for mode in ("lines", "characters"):
            with self.subTest(mode=mode):
                self.job_id = ("c" if mode == "lines" else "d") * 32
                self.start(capture_mode=mode)
                first = "page one\r\n --More-- "
                second = "\b" * 9 + "page two\r\n --More-- "
                last = "\b" * 9 + "last page\r\nrouter#"
                self.screen.pending = first
                self.screen.pages = [second, last]
                started = self.screen.now
                self.engine.pump()
                result = self.result()
                self.assertEqual(result["status"], "completed")
                self.assertEqual(result["pages_advanced"], 2)
                self.assertEqual(result["output"], first + second + last)
                self.assertFalse(result["capture_may_be_incomplete"])
                self.assertLess(self.screen.now - started, 1.1)

    def test_pause_keeps_line_capture_and_warning(self):
        self.start()
        self.screen.pending = "Building configuration...\r\nunfinished fragment"
        self.engine.pump()
        self.assertEqual(self.result()["capture_mode"], "lines")
        self.assertTrue(self.result()["capture_may_be_incomplete"])
        self.screen.pending = "next page\r\n --More-- "
        self.screen.pages = ["\b" * 9 + "end\r\nrouter#"]
        self.engine.pump()
        self.assertEqual(self.result()["status"], "completed")
        self.assertEqual(self.result()["capture_mode"], "lines")
        self.assertTrue(self.result()["capture_may_be_incomplete"])

    def test_manual_paging(self):
        self.start(auto_paging=False)
        self.screen.pending = "page\r\n --More-- "
        self.engine.pump()
        self.assertTrue(self.result()["waiting_for_input"])
        self.assertEqual(self.screen.sent, ["show version\r"])
        self.engine.send_input({"job_id": self.job_id, "text": " "})
        self.assertEqual(self.screen.sent[-1], " ")

    def test_no_duplicate_space_on_slow_reply(self):
        self.start(capture_mode="characters")
        self.screen.pending = "page\r\n--More-- "
        self.engine.pump()
        self.screen.pending = " "
        self.engine.pump()
        self.engine.pump()
        self.assertEqual(self.screen.sent, ["show version\r", " "])

    def test_questions_and_prose_are_not_answered(self):
        self.start(capture_mode="characters")
        for text in ("Continue? [y/N]", "\r\nDescription of --More--", "\r\n[confirm]"):
            self.screen.pending = text
            self.engine.pump()
        self.assertEqual(self.screen.sent, ["show version\r"])

    def test_interrupt_stops_pager_advance(self):
        self.start()
        self.engine.interrupt({"job_id": self.job_id})
        self.screen.pending = "\r\n--More--"
        self.engine.pump()
        self.assertEqual(self.screen.sent, ["show version\r", "\x03"])

    def test_pager_send_failure_is_not_retried(self):
        self.start()
        self.screen.pending = "\r\n--More--"
        with patch.object(self.screen, "Send", side_effect=RuntimeError("send failed")) as send:
            self.engine.pump()
            self.engine.pump()
        send.assert_called_once_with(" ")
        self.assertEqual(self.result()["status"], "failed")

    def test_cursor_pages_and_truncation(self):
        self.start()
        text = "ž🙂\n" * 4000 + "router#"
        with patch.object(core, "MAX_OUTPUT", 10000):
            self.screen.pending = text
            self.engine.pump()
        pieces = []
        cursor = 0
        while True:
            result = self.result(cursor=cursor, limit=997)
            pieces.append(result["output"])
            cursor = result["next_cursor"]
            if not result["has_more"]:
                break
        self.assertEqual("".join(pieces), text[:10000])
        self.assertTrue(result["truncated"])
        self.assertEqual(result["status"], "completed")

    def test_invalid_options_send_nothing(self):
        for options in ({"auto_paging": "yes"}, {"capture_mode": "invalid"}):
            with self.assertRaises(ValueError):
                self.start(**options)
        self.assertEqual(self.screen.sent, [])


if __name__ == "__main__":
    unittest.main()
