"""Render a portable report with escaped data and local search."""

from html import escape


def render_page(title, eyebrow, scope, times, cards, columns, rows, note):
    """Build an accessible, self-contained HTML table without remote resources."""

    def safe(value):
        """Escape an untrusted value for HTML text or attribute content."""
        return escape(str(value), quote=True)

    card_html = "".join(
        f"<article><strong>{safe(value)}</strong><span>{safe(label)}</span></article>"
        for label, value in cards
    )
    header = "".join(f'<th scope="col">{safe(column)}</th>' for column in columns)
    body = "".join(
        "<tr>" + "".join(f"<td>{safe(cell)}</td>" for cell in row) + "</tr>"
        for row in rows
    )
    return f"""<!doctype html>
<html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; connect-src 'none'; img-src 'none'; base-uri 'none'; form-action 'none'">
<title>{safe(title)} — {safe(scope)}</title>
<style>
:root{{font-family:ui-sans-serif,system-ui,sans-serif;color:#233333;background:#f3f5f0}}
*{{box-sizing:border-box}}body{{margin:0}}main{{max-width:1500px;margin:auto;padding:48px 32px}}
.eyebrow{{font-size:12px;letter-spacing:.14em;text-transform:uppercase;color:#49645b;font-weight:700}}
h1{{font-size:clamp(34px,5vw,60px);font-weight:650;letter-spacing:-.045em;margin:10px 0}}
.scope{{font-size:20px;margin:0 0 10px}}.time{{font-size:13px;color:#52645f}}.cards{{display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin:32px 0}}
article{{border:1px solid #d4ded3;background:#fff;border-radius:12px;padding:23px}}article strong{{font-size:38px;display:block;color:#204e3f}}article span{{display:block;font-size:13px;margin-top:7px}}
.note{{padding:18px 22px;border-left:4px solid #b0782d;background:#fff9ee;font-size:14px;line-height:1.6;margin-bottom:28px}}
.tools{{display:flex;align-items:center;justify-content:space-between;gap:16px;margin-bottom:14px}}label{{font-weight:600;font-size:14px}}input{{margin-left:10px;padding:11px 14px;border-radius:7px;border:1px solid #98afa2;width:260px;font:inherit}}
.table{{overflow:auto;background:white;border:1px solid #d4ded3;border-radius:12px}}table{{width:100%;border-collapse:collapse;text-align:left;font-size:13px}}th{{background:#e7ede4;font-size:11px;text-transform:uppercase;letter-spacing:.05em;padding:16px;white-space:nowrap}}td{{padding:16px;border-top:1px solid #e5eae2;vertical-align:top;max-width:360px;overflow-wrap:anywhere;min-width:110px}}td:first-child{{font-weight:650;color:#285643;text-transform:capitalize}}tr[hidden]{{display:none}}
footer{{margin-top:24px;color:#52645f;font-size:12px}}#empty{{padding:20px}}@media(max-width:700px){{main{{padding:28px 16px}}.cards{{grid-template-columns:repeat(2,1fr)}}.tools{{display:block}}input{{width:190px}}}}
@media print{{input,.tools{{display:none}}main{{padding:0}}.table{{overflow:visible}}table{{font-size:9px}}td,th{{padding:6px;min-width:0}}}}
</style><main><div class="eyebrow">{safe(eyebrow)}</div><h1>{safe(title)}</h1>
<p class="scope">{safe(scope)}</p><div class="time">{safe(times)}</div><section class="cards" aria-label="Summary">{card_html}</section>
<aside class="note">{safe(note)}</aside><div class="tools"><label>Search findings<input id="search" type="search" placeholder="Device, check, account…"></label><span id="count" role="status">{len(rows)} rows</span></div>
<div class="table"><table><thead><tr>{header}</tr></thead><tbody>{body}</tbody></table><p id="empty" hidden>No matching rows.</p></div>
<footer>Local export review · No external scripts, uploads, or telemetry. Source values are displayed as text.</footer></main>
<script>const rows=[...document.querySelectorAll('tbody tr')];document.querySelector('#search').addEventListener('input',event=>{{let visible=0;const term=event.target.value.toLowerCase();for(const row of rows){{row.hidden=!row.textContent.toLowerCase().includes(term);if(!row.hidden)visible++;}}document.querySelector('#count').textContent=visible+' of '+rows.length+' rows';document.querySelector('#empty').hidden=visible!==0;}});</script></html>"""
