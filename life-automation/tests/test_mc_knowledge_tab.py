"""Browser tests for Mission Control Knowledge tab.
Uses Playwright for automated browser verification.
Marked local_only — requires running MC containers."""
import os

import pytest

try:
    from playwright.sync_api import sync_playwright
    _HAS_PLAYWRIGHT = True
except ImportError:
    _HAS_PLAYWRIGHT = False

MC_URL = os.environ.get("MC_URL", "http://localhost:3000")


@pytest.mark.skipif(not _HAS_PLAYWRIGHT, reason="playwright not installed")
@pytest.mark.local_only
class TestMCKnowledgeTab:
    def test_knowledge_tab_loads_tree(self):
        """Knowledge tab loads and shows PARA tree."""
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto(MC_URL)
            page.click('[data-tab="knowledge"]')
            page.wait_for_selector("#knowledge-tree", timeout=5000)
            tree_text = page.locator("#knowledge-tree").inner_text()
            assert "Projects" in tree_text
            browser.close()

    def test_knowledge_tree_shows_entities(self):
        """Tree shows pi-cluster and other entities."""
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto(MC_URL)
            page.click('[data-tab="knowledge"]')
            page.wait_for_selector("#knowledge-tree", timeout=5000)
            tree_text = page.locator("#knowledge-tree").inner_text()
            assert "pi-cluster" in tree_text
            browser.close()

    def test_knowledge_file_viewer(self):
        """Loading a file via JS renders content in the viewer."""
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto(MC_URL)
            page.click('[data-tab="knowledge"]')
            page.wait_for_selector("#knowledge-tree", timeout=5000)
            # Load a file directly via the JS function
            page.evaluate("loadKnowledgeFile('Projects/pi-cluster/summary.md')")
            page.wait_for_timeout(3000)
            content = page.locator("#knowledge-content").inner_text()
            assert len(content) > 50
            assert "pi-cluster" in content.lower() or "cluster" in content.lower()
            browser.close()

    def test_knowledge_screenshot(self):
        """Take a screenshot of the Knowledge tab for visual verification."""
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1280, "height": 800})
            page.goto(MC_URL)
            page.click('[data-tab="knowledge"]')
            page.wait_for_selector("#knowledge-tree", timeout=5000)
            page.wait_for_timeout(1000)
            page.screenshot(path="/tmp/mc-knowledge-tab.png")
            browser.close()
