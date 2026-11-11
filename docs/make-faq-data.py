#!/usr/bin/env python3
"""Fills index.html's FAQ structured data (for search engines) from its visible
English FAQ. Run after editing the FAQ; the tests check that the two match."""

import html
import json
import pathlib
import re

PAGE = pathlib.Path(__file__).resolve().parent / "index.html"
SLOT = re.compile(r'(<script type="application/ld\+json" id="faq-data">)(.*?)(</script>)', re.S)


def english(fragment):
    text = re.search(r'<span lang="en">(.*?)</span>(?=<span lang="bn">)', fragment, re.S).group(1)
    return html.unescape(re.sub(r"<[^>]+>", "", text)).strip()


def faq_data(page):
    faq = page[page.index('<div class="faq">'):]
    items = re.findall(r"<summary>(.*?)</summary>\s*<p>(.*?)</p>", faq, re.S)
    return {
        "@context": "https://schema.org",
        "@type": "FAQPage",
        "mainEntity": [{"@type": "Question", "name": english(q),
                        "acceptedAnswer": {"@type": "Answer", "text": english(a)}} for q, a in items],
    }


def generate(page):
    data = json.dumps(faq_data(page), indent=2, ensure_ascii=False)
    return SLOT.sub(lambda m: f"{m.group(1)}\n{data}\n{m.group(3)}", page, count=1)


if __name__ == "__main__":
    PAGE.write_text(generate(PAGE.read_text()))
    print("Updated the FAQ data in index.html")
