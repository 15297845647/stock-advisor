import unittest
from tests.fixtures.research import snapshot
from tests.test_research_repository import run_fixture


class EvidenceReportTests(unittest.TestCase):
    def setUp(self):
        import domain.report_renderer as renderer
        self.assertTrue(hasattr(renderer,'render_research_report'))
        self.render=renderer.render_research_report

    def test_source_time_and_unknown_fields(self):
        s=snapshot(); r=run_fixture(s)
        s.missing_fields=['fundamentals']
        text=self.render(r,s)
        for token in ('2026-10-22','fixture','fundamentals','规则评分','失效','r1'):
            self.assertIn(token,text)
        self.assertNotIn('置信度',text)

    def test_unknown_evidence_rejected(self):
        s=snapshot();r=run_fixture(s)
        r.decision.supporting_evidence_ids=['not-real']
        with self.assertRaises(ValueError): self.render(r,s)

    def test_html_header_is_escaped(self):
        from domain.report_renderer import ReportRenderer
        html=ReportRenderer().render_html('600000','<script>alert(1)</script>','quick','<img src=x onerror=alert(1)>')
        self.assertNotIn('<script>',html)
        self.assertNotIn('<img src=x',html)
