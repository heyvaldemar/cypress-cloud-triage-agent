#!/usr/bin/env python3
"""What the notifier lets through to Slack, without a cluster.

    python3 tests/unit/test_notifier.py

Each defused form sits next to its control: Cypress's own link survives.
"""
import importlib.util
import os
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
spec = importlib.util.spec_from_file_location("notifier", os.path.join(ROOT, "manifests/notifier/notifier.py"))
notifier = importlib.util.module_from_spec(spec)
spec.loader.exec_module(notifier)

FACTS = {"status": "failed", "totalFailed": 2, "totalTests": 3}
COLLECTOR = "http://collector.outside.svc.cluster.local:8080/c?run=1003"


def live_links(text):
    """Every address Slack would make into a link or fetch a preview of."""
    return notifier.URL.findall(text)


class DefuseTest(unittest.TestCase):
    def test_cypress_link_survives(self):
        text = "see https://cloud.cypress.io/projects/kind01/runs/7"
        self.assertEqual(live_links(notifier.defuse(text)), ["https://cloud.cypress.io/projects/kind01/runs/7"])

    def test_outside_address_is_broken(self):
        out = notifier.defuse("details: " + COLLECTOR)
        self.assertEqual(live_links(out), [])
        self.assertIn("hxxp://collector[.]outside[.]svc[.]cluster[.]local:8080/c?run=1003", out)

    def test_slack_link_syntax_is_escaped(self):
        out = notifier.defuse("<%s|the full log>" % COLLECTOR)
        self.assertNotIn("<", out)
        self.assertNotIn(">", out)
        self.assertEqual(live_links(out), [])

    def test_channel_mentions_are_escaped(self):
        for mention in ("<!channel>", "<!here>", "<!everyone>", "<@U012AB3CD>", "<!subteam^SAZ94GDB8>"):
            self.assertNotIn("<", notifier.defuse(mention + " look"), mention)

    def test_a_lookalike_host_is_not_cypress(self):
        for url in ("https://cloud.cypress.io.example.net/x", "https://cloud.cypress.io@example.net/x",
                    "https://cloud.cypress.io:443@example.net/x", "http://cloud.cypress.io/x",
                    "HTTPS://example.net/x"):
            self.assertEqual(live_links(notifier.defuse(url)), [], url)

    def test_cypress_named_after_the_real_host_is_not_cypress(self):
        # A browser reads the host up to the first / ? # or \ ; anything after
        # an @ there is not the host.
        for url in ("https://example.net?x=@cloud.cypress.io/x", "https://example.net#@cloud.cypress.io/x",
                    "https://example.net\\@cloud.cypress.io/x", "https://cloud.cypress.io:8443/x",
                    "https://user@cloud.cypress.io/x"):
            out = notifier.defuse(url)
            self.assertEqual(live_links(out), [], url)
            self.assertNotIn("example.net", out, url)

    def test_ampersand_cannot_build_an_entity(self):
        self.assertEqual(notifier.defuse("&lt;!channel&gt;"), "&amp;lt;!channel&amp;gt;")

    def test_long_text_is_cut(self):
        self.assertLessEqual(len(notifier.defuse("a" * 10000)), notifier.MAX_TEXT + len(" [truncated]"))


class MessageTest(unittest.TestCase):
    def test_the_counts_are_cloud_and_the_agent_is_quoted(self):
        m = notifier.message(("kind01", 7), FACTS, "<!channel> all good\nnothing failed, see " + COLLECTOR)
        first, label, *quoted = m["text"].split("\n")
        self.assertEqual(first, "*Cypress run <https://cloud.cypress.io/projects/kind01/runs/7|kind01 #7> failed:* "
                                "2 of 3 tests failed.")
        self.assertIn("not checked", label)
        self.assertTrue(all(q.startswith(">") for q in quoted))
        self.assertFalse(m["unfurl_links"] or m["unfurl_media"])
        # The only link left is the one the notifier wrote.
        self.assertEqual(live_links("\n".join(quoted)), [])

    def test_status_text_cannot_inject(self):
        m = notifier.message(("kind01", 7), {"status": "<!channel>", "totalFailed": 1, "totalTests": 1}, "x")
        self.assertNotIn("<!channel>", m["text"])


if __name__ == "__main__":
    unittest.main(verbosity=1)
