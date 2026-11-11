"""Checks the website stays consistent: bilingual, and its search data matches the page."""

import importlib.util
import json
import re
import unittest
from pathlib import Path

DOCS = Path(__file__).resolve().parent.parent / "docs"
PAGE = (DOCS / "index.html").read_text()


class WebsiteTest(unittest.TestCase):
    def test_every_english_text_has_a_bangla_twin(self):
        body = PAGE[PAGE.index("<body>"):]
        self.assertEqual(body.count('<span lang="en">'), body.count('<span lang="bn">'))

    def test_faq_data_matches_the_visible_faq(self):
        spec = importlib.util.spec_from_file_location("make_faq_data", DOCS / "make-faq-data.py")
        maker = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(maker)
        self.assertEqual(maker.generate(PAGE), PAGE, "FAQ data is out of date: run docs/make-faq-data.py")

    def test_structured_data_is_valid_json(self):
        blocks = re.findall(r'<script type="application/ld\+json"[^>]*>(.*?)</script>', PAGE, re.S)
        self.assertEqual({json.loads(block)["@type"] for block in blocks}, {"SoftwareApplication", "FAQPage"})


    def test_download_links_fetch_the_file_itself(self):
        # Not the releases page: that makes you hunt for the file. CI uploads this name.
        links = re.findall(r"<a [^>]*data-download[^>]*>", PAGE)
        self.assertEqual(len(links), 3)
        for link in links:
            self.assertIn('href="https://github.com/saikatnjm/dimly/releases/latest/download/dimly_latest_all.deb"', link)
        self.assertIn("dimly_latest_all.deb", (DOCS.parent / ".github" / "workflows" / "ci.yml").read_text())


if __name__ == "__main__":
    unittest.main()
