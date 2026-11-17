"""Run the notification island's pure model through Qt's JavaScript engine."""
import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
MODEL = ROOT / "NotificationIslandModel.js"
ISLAND = ROOT / "NotificationIsland.qml"

FIXTURE = r"""
import QtQuick
import QtTest
import "MODEL_URL" as IslandModel

TestCase {
    name: "NotificationIslandModel"

    function entry(key, ts, extra) {
        var out = { key: key, app: "App", appIcon: "", summary: "Summary " + key, body: "Body", urgency: 1, ts: ts }
        for (var k in (extra || {})) out[k] = extra[k]
        return out
    }

    function test_parse_rejects_garbage() {
        compare(IslandModel.parseLive("").length, 0)
        compare(IslandModel.parseLive("not json").length, 0)
        compare(IslandModel.parseLive("{\"key\":\"n1\"}").length, 0)
        compare(IslandModel.parseLive(JSON.stringify([null, 3, [], { key: "../x", summary: "s" },
                                                      { key: "n1" }])).length, 0)
        compare(IslandModel.parseLive(" ".repeat(IslandModel.MAX_FILE_CHARS + 1)).length, 0)
    }

    function test_parse_bounds_and_neutralises_text() {
        var rows = IslandModel.parseLive(JSON.stringify([entry("n1", 10, {
            app: "A‮B\u0007C",
            appIcon: "/etc/passwd",
            summary: "<img src=\"https://example.invalid/t\">  two\nlines",
            body: "<b>bold</b> &amp; &lt;tag&gt; " + "x".repeat(1000),
            urgency: 9
        })]))
        compare(rows.length, 1)
        compare(rows[0].app, "A B C")
        compare(rows[0].appIcon, "")
        // The summary is plain text by spec: markup stays literal.
        compare(rows[0].summary, "<img src=\"https://example.invalid/t\"> two lines")
        verify(rows[0].body.indexOf("bold & <tag> x") === 0)
        verify(rows[0].body.length <= IslandModel.MAX_BODY)
        compare(rows[0].urgency, 2)
    }

    function test_first_read_seeds_without_showing() {
        var rows = IslandModel.parseLive(JSON.stringify([entry("n1", 100)]))
        var result = IslandModel.arrivals(null, rows, 100)
        compare(result.show, null)
        verify(result.seen.n1)
    }

    function test_new_recent_row_shows_newest_unseen() {
        var seen = { n1: true }
        var rows = IslandModel.parseLive(JSON.stringify([entry("n1", 100), entry("n2", 101), entry("n3", 102)]))
        var result = IslandModel.arrivals(seen, rows, 103)
        compare(result.show.key, "n3")
        compare(result.fresh.map(function(r) { return r.key }), ["n2", "n3"])
        verify(result.seen.n2 && result.seen.n3)
        var again = IslandModel.arrivals(result.seen, rows, 103)
        compare(again.show, null)
        compare(again.fresh.length, 0)
    }

    function test_stack_puts_newest_on_top_and_is_bounded() {
        function keys(stack) { return stack.map(function(r) { return r.key }) }
        var rows = IslandModel.parseLive(JSON.stringify([entry("n1", 1), entry("n2", 2), entry("n3", 3),
                                                         entry("n4", 4)]))
        var stack = IslandModel.stacked([], [rows[0]])
        compare(keys(stack), ["n1"])
        stack = IslandModel.stacked(stack, [rows[1], rows[2]])
        compare(keys(stack), ["n3", "n2", "n1"])
        // Bounded: the oldest card drops off the bottom.
        stack = IslandModel.stacked(stack, [rows[3]])
        compare(keys(stack), ["n4", "n3", "n2"])
        compare(stack.length, IslandModel.MAX_STACK)
        // A key already in the stack is not shown twice.
        compare(keys(IslandModel.stacked(stack, [rows[3]])), ["n4", "n3", "n2"])
        compare(IslandModel.stacked(null, null).length, 0)
    }

    function test_old_rows_are_not_news() {
        var rows = IslandModel.parseLive(JSON.stringify([entry("n1", 100)]))
        compare(IslandModel.arrivals({}, rows, 100 + IslandModel.MAX_AGE_SECONDS + 1).show, null)
    }

    function test_removed_rows_leave_seen() {
        var rows = IslandModel.parseLive(JSON.stringify([entry("n2", 100)]))
        var result = IslandModel.arrivals({ n1: true, n2: true }, rows, 100)
        verify(!result.seen.n1)
        verify(result.seen.n2)
    }

    function test_newest_key_and_lines() {
        var rows = IslandModel.parseLive(JSON.stringify([entry("n1", 1), entry("n2", 2, { summary: "" })]))
        compare(IslandModel.newestKey(rows), "n2")
        compare(IslandModel.newestKey([]), "")
        compare(IslandModel.headline(rows[1]), "Body")
        compare(IslandModel.detail(rows[1]), "")
        compare(IslandModel.detail(rows[0]), "Body")
    }

    function test_activation_needs_newest_row_and_matching_host_count() {
        var rows = IslandModel.parseLive(JSON.stringify([entry("n1", 1), entry("n2", 2)]))
        verify(IslandModel.mayActivate(rows, "n2", 2))
        // A newer row, or a toast the snapshot does not hold (transient), or a
        // held/leaving row the host does not count: the click only closes.
        verify(!IslandModel.mayActivate(rows, "n1", 2))
        verify(!IslandModel.mayActivate(rows, "n2", 3))
        verify(!IslandModel.mayActivate(rows, "n2", 1))
        verify(!IslandModel.mayActivate(rows, "n2", -1))
        verify(!IslandModel.mayActivate(rows, "", 2))
    }

    function test_hold_and_icons() {
        verify(IslandModel.holdMs(2) > IslandModel.holdMs(1))
        verify(IslandModel.holdMs(1) > IslandModel.holdMs(0))
        compare(IslandModel.iconNames({ appIcon: "firefox", app: "Google Chrome" }),
                ["firefox", "google-chrome"])
        compare(IslandModel.iconNames({ appIcon: "", app: "../../x" }), ["x"])
    }
}
"""


class NotificationIslandTest(unittest.TestCase):
    def test_model(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = Path(directory) / "tst_notification_island.qml"
            fixture.write_text(FIXTURE.replace("MODEL_URL", MODEL.as_uri()))
            result = subprocess.run(
                ["/usr/lib/qt6/bin/qmltestrunner", "-input", str(fixture)],
                env={**os.environ, "QT_QPA_PLATFORM": "offscreen", "QT_QUICK_BACKEND": "software"},
                capture_output=True, text=True,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_every_island_text_is_plain(self):
        source = ISLAND.read_text()
        blocks = re.findall(r"\bText \{(.*?)\n\s*\}", source, re.S)
        self.assertGreater(len(blocks), 0)
        for block in blocks:
            self.assertIn("textFormat: Text.PlainText", block)


if __name__ == "__main__":
    unittest.main()
