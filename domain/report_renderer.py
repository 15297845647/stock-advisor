"""报告渲染器 — 把分析结果结构化为 Markdown / HTML

单一职责：文本模板渲染，不做 IO。
"""

from datetime import datetime


class ReportRenderer:
    """报告 Markdown + HTML 渲染器"""

    def render_html(
        self, stock_code: str, stock_name: str, depth: str,
        report_content: str,
    ) -> str:
        """把 markdown 报告包装成完整 HTML 页面"""
        body_html = self._markdown_to_html(report_content)
        ts = datetime.now().strftime("%Y-%m-%d %H:%M")
        return self._html_template(
            stock_name, stock_code, depth, body_html, ts,
        )

    def render_pdf_html(
        self, stock_code: str, stock_name: str, depth: str,
        report_content: str,
    ) -> str:
        """PDF 专用 HTML（打印样式，去掉导航等）"""
        body_html = self._markdown_to_html(report_content)
        ts = datetime.now().strftime("%Y-%m-%d %H:%M")
        return self._pdf_template(
            stock_name, stock_code, depth, body_html, ts,
        )

    # ── 简易 markdown → HTML（无依赖）──

    @staticmethod
    def _markdown_to_html(text: str) -> str:
        """极简 markdown → HTML（够用，避免加依赖）"""
        lines = text.split("\n")
        html_lines: list[str] = []
        in_ul = False

        for raw in lines:
            line = raw.rstrip()
            if not line:
                if in_ul:
                    html_lines.append("</ul>")
                    in_ul = False
                html_lines.append("<br/>")
                continue

            if line.startswith("## "):
                if in_ul:
                    html_lines.append("</ul>")
                    in_ul = False
                html_lines.append(f"<h2>{ReportRenderer._escape(line[3:])}</h2>")
            elif line.startswith("# "):
                html_lines.append(f"<h1>{ReportRenderer._escape(line[2:])}</h1>")
            elif line.startswith("- ") or line.startswith("* "):
                if not in_ul:
                    html_lines.append("<ul>")
                    in_ul = True
                html_lines.append(f"<li>{ReportRenderer._escape(line[2:])}</li>")
            else:
                if in_ul:
                    html_lines.append("</ul>")
                    in_ul = False
                html_lines.append(f"<p>{ReportRenderer._escape(line)}</p>")

        if in_ul:
            html_lines.append("</ul>")
        return "\n".join(html_lines)

    @staticmethod
    def _escape(text: str) -> str:
        """HTML 转义"""
        return (
            text.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
        )

    # ── HTML 模板 ──

    @staticmethod
    def _html_template(
        name: str, code: str, depth: str, body: str, ts: str,
    ) -> str:
        return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{name}（{code}） · Stock Advisor 分析报告</title>
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, "PingFang SC", "Microsoft YaHei", sans-serif;
          max-width: 800px; margin: 20px auto; padding: 20px; color: #333; line-height: 1.7; }}
  .header {{ border-bottom: 2px solid #2563eb; padding-bottom: 12px; margin-bottom: 20px; }}
  .header h1 {{ margin: 0; color: #1e40af; }}
  .meta {{ color: #6b7280; font-size: 14px; margin-top: 6px; }}
  .depth-badge {{ display: inline-block; background: #dbeafe; color: #1e40af;
                  padding: 2px 10px; border-radius: 12px; font-size: 12px; margin-left: 8px; }}
  h1 {{ font-size: 22px; }} h2 {{ font-size: 18px; color: #374151;
                                  border-left: 4px solid #2563eb; padding-left: 10px; margin-top: 24px; }}
  p {{ margin: 4px 0; }}
  ul {{ margin: 6px 0; padding-left: 24px; }}
  .footer {{ margin-top: 40px; padding-top: 12px; border-top: 1px solid #e5e7eb;
             color: #9ca3af; font-size: 12px; text-align: center; }}
</style>
</head>
<body>
  <div class="header">
    <h1>{name}（{code}）<span class="depth-badge">{depth}</span></h1>
    <div class="meta">生成时间: {ts}</div>
  </div>
  <div class="content">
    {body}
  </div>
  <div class="footer">
    ⚠️ 本报告由 AI 生成，仅供参考，不构成投资建议。投资有风险，决策需谨慎。<br/>
    Stock Advisor · A股/期货 AI 分析系统
  </div>
</body>
</html>"""

    @staticmethod
    def _pdf_template(
        name: str, code: str, depth: str, body: str, ts: str,
    ) -> str:
        """PDF 打印样式版本（预留给 weasyprint 用）"""
        return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<style>
  @page {{ size: A4; margin: 2cm; }}
  body {{ font-family: "PingFang SC", "Microsoft YaHei", sans-serif;
          color: #333; line-height: 1.7; font-size: 12pt; }}
  h1 {{ color: #1e40af; }}
  h2 {{ font-size: 14pt; border-left: 3px solid #2563eb; padding-left: 8px; }}
  .footer {{ margin-top: 30px; padding-top: 10px; border-top: 1px solid #ccc;
             font-size: 9pt; color: #666; }}
</style>
</head>
<body>
<h1>{name}（{code}） · {depth}分析</h1>
<div class="meta">生成时间: {ts}</div>
<hr/>
{body}
<div class="footer">
⚠️ 本报告由 AI 生成，仅供参考，不构成投资建议。投资有风险，决策需谨慎。
</div>
</body>
</html>"""
