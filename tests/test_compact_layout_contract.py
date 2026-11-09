"""Regression assertions for the dense, responsive workspace layout."""
from html.parser import HTMLParser
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1] / 'web' / 'visualization'


class _Structure(HTMLParser):
    def __init__(self):
        super().__init__()
        self.elements = []
        self.stack = []

    def handle_starttag(self, tag, attrs):
        attr = dict(attrs)
        self.elements.append((tag, attr, tuple(self.stack)))
        if tag not in {'input', 'img', 'link', 'meta', 'br', 'hr', 'source'}:
            self.stack.append((tag, attr.get('class', '')))

    def handle_endtag(self, tag):
        if self.stack:
            for index in range(len(self.stack) - 1, -1, -1):
                if self.stack[index][0] == tag:
                    del self.stack[index:]
                    break


class CompactLayoutContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.browser = (ROOT / 'browser.html').read_text(encoding='utf-8')
        cls.runner = (ROOT / 'runner.html').read_text(encoding='utf-8')
        cls.css = (ROOT / 'styles-light.css').read_text(encoding='utf-8')

    def test_browser_metadata_is_expandable_not_discarded(self):
        self.assertIn('class="compact-details browser-metadata"', self.browser)
        self.assertIn('id="datasetMeta"', self.browser)
        self.assertIn('class="browser-single-value-note"', self.browser)
        self.assertIn('id="primaryTableSelect"', self.browser)

    def test_training_filters_share_a_responsive_grid(self):
        self.assertIn('class="runner-identity-grid"', self.runner)
        self.assertIn('repeat(4, minmax(0, 1fr))', self.css)
        for name in ('modelFilter', 'subsystemFilter', 'deviceFilter', 'variableFilter'):
            self.assertIn('id="' + name + '"', self.runner)

    def test_history_is_a_disclosure_not_removed(self):
        for name in ('trainingHistoryList', 'evaluationHistoryList'):
            self.assertIn('id="' + name + '"', self.runner)
        self.assertEqual(self.runner.count('class="panel runner-history-panel"'), 2)
        self.assertIn('class="runner-search-summary"', self.runner)

    def test_compactness_preserves_chart_when_populated(self):
        self.assertIn('.runner-result-chart:has(> .empty-state)', self.css)
        self.assertIn('.browser-page #chart:has(> .empty-state)', self.css)
        self.assertIn('@media (max-width: 430px)', self.css)
        self.assertIn('layout_compact=1', self.browser)
        self.assertIn('layout_compact=1', self.runner)
