from html import escape
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import HRFlowable, KeepTogether, PageBreak, Paragraph, Preformatted, SimpleDocTemplate, Spacer, Table, TableStyle

OUT = Path("output/pdf/agent8088-cli-anything-drawio-findings.pdf")
NAVY = colors.HexColor("#16324F")
BLUE = colors.HexColor("#237DD7")
TEAL = colors.HexColor("#0B7A75")
LIGHT = colors.HexColor("#F3F6F9")
MID = colors.HexColor("#D8E0E8")
DARK = colors.HexColor("#243447")
ss = getSampleStyleSheet()
ss.add(ParagraphStyle(name="K", fontName="Helvetica-Bold", fontSize=9, leading=12, textColor=BLUE, alignment=1, spaceAfter=10))
ss.add(ParagraphStyle(name="T", fontName="Helvetica-Bold", fontSize=27, leading=31, textColor=NAVY, alignment=1, spaceAfter=10))
ss.add(ParagraphStyle(name="CS", fontName="Helvetica", fontSize=12, leading=17, textColor=DARK, alignment=1, spaceAfter=22))
ss.add(ParagraphStyle(name="H", fontName="Helvetica-Bold", fontSize=16, leading=20, textColor=NAVY, spaceBefore=10, spaceAfter=8))
ss.add(ParagraphStyle(name="B", fontName="Helvetica", fontSize=9.2, leading=13.3, textColor=DARK, spaceAfter=6))
ss.add(ParagraphStyle(name="S", fontName="Helvetica", fontSize=8, leading=10.5, textColor=DARK, spaceAfter=3))
ss.add(ParagraphStyle(name="TX", fontName="Helvetica", fontSize=7.8, leading=10, textColor=DARK))
ss.add(ParagraphStyle(name="TB", fontName="Helvetica-Bold", fontSize=7.8, leading=10, textColor=DARK))
ss.add(ParagraphStyle(name="TH", fontName="Helvetica-Bold", fontSize=7.8, leading=10, textColor=colors.white))
ss.add(ParagraphStyle(name="FT", fontName="Helvetica-Bold", fontSize=9, leading=12, textColor=NAVY, spaceAfter=2))
ss.add(ParagraphStyle(name="C", fontName="Courier", fontSize=7.3, leading=9.3, textColor=DARK, backColor=LIGHT, borderColor=MID, borderWidth=.5, borderPadding=5, leftIndent=4, rightIndent=4, spaceBefore=3, spaceAfter=6))

def p(x, style="B"):
    return Paragraph(escape(x).replace("\n", "<br/>"), ss[style])

def tx(x):
    return p(x, "TX")

def tb(x):
    return p(x, "TB")

def th(x):
    return p(x, "TH")

def grid(data, widths, header=NAVY):
    t = Table(data, colWidths=widths, repeatRows=1)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), header), ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, LIGHT]), ("BOX", (0, 0), (-1, -1), .6, MID),
        ("INNERGRID", (0, 0), (-1, -1), .35, MID), ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 5), ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    return t

def meta(data, widths):
    t = Table(data, colWidths=widths)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (0, -1), LIGHT), ("BOX", (0, 0), (-1, -1), .6, MID),
        ("INNERGRID", (0, 0), (-1, -1), .35, MID), ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 8), ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 7), ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
    ]))
    return t

story = [Spacer(1, 24 * mm), p("QA FINDINGS REPORT", "K"), p("Agent8088 CLI-Anything Integration", "T"), p("Manual Draw.io end-to-end test results", "CS"), HRFlowable(width="70%", thickness=2, color=BLUE, spaceAfter=18)]
story.append(meta([[tb("Commit"), tx("edfc802d1ade696cdece03ab8e1d4de2321da808")], [tb("Branch"), tx("CLI_Anything_Dev")], [tb("Application"), tx("Draw.io Desktop + cli-anything-drawio")], [tb("Test date"), tx("2026-08-24")], [tb("Environment"), tx("macOS, Python 3.13, isolated temporary runtime")]], [34 * mm, 116 * mm]))
story += [Spacer(1, 18 * mm), p("Release assessment: NOT READY", "K"), p("The Draw.io workflow passed after temporary environment workarounds. The commit remains blocked by installer, permission, Git environment, documentation, and error-handling defects.", "CS"), PageBreak()]

story += [p("1. Executive summary", "H"), p("A complete Draw.io workflow was exercised through Agent8088's cli_anything_run tool. The test created a project, added and edited shapes, connected them with a Unicode label, reopened the project, exported SVG and PNG files, visually verified the PNG, tested dry-run behavior, exercised a path containing spaces, updated the harness, uninstalled it, and verified execution was rejected afterward."), p("The functional path passed only after manually bootstrapping pip into the managed virtual environment, configuring Git HTTP/1.1 inside Agent8088's private HOME, and using full-auto mode. Those workarounds are evidence of integration defects, not product passes."), p("2. Test results", "H")]
story.append(grid([[th("Area"), th("Coverage"), th("Result")], [tx("Runtime lifecycle"), tx("status, setup, install, update, uninstall, post-uninstall status"), tx("PASS with workarounds")], [tx("Catalog and skill"), tx("search, info, packaged SKILL.md loading"), tx("PASS")], [tx("Execution"), tx("--help, structured argv, absolute cwd, Unicode, path with spaces"), tx("PASS")], [tx("Project model"), tx("create, reopen, inspect, shape add, connector add, label mutation"), tx("PASS")], [tx("Exports"), tx("SVG, PNG, crop, dimensions, visual render inspection"), tx("PASS")], [tx("Safety and errors"), tx("approval retry, dry-run, missing project, uninstall rejection"), tx("PARTIAL FAIL")]], [32 * mm, 93 * mm, 25 * mm]))
story += [p("3. Successful workflow evidence", "H")]
story.append(grid([[th("Evidence"), th("Observed result")], [tx("Project creation"), tx("architecture.json created with letter preset and 850 x 1100 canvas.")], [tx("Diagram state"), tx("Two shapes: start/Begin and finish/Finish. One orthogonal connector start-to-finish with a Unicode label.")], [tx("Exports"), tx("architecture.svg: 13,040 bytes. architecture.png: 16,099 bytes. Visual inspection showed the expected rectangle, ellipse, arrow, and label.")], [tx("Argument safety"), tx("A path containing a space, architecture copy.svg, exported successfully as one argument.")], [tx("Dry-run"), tx("A simulated label update did not persist; finish remained Finish on subsequent inspection.")], [tx("Lifecycle cleanup"), tx("Update and uninstall succeeded. CLI-Hub remained available at version 0.4.1. Running Draw.io after uninstall returned a controlled not-installed error.")]], [36 * mm, 114 * mm], TEAL))
story.append(PageBreak())

story.append(p("4. Findings requiring fixes", "H"))
findings = [("F-01 | Suggested severity: High | Approved local setup remains blocked", "In read-only mode, cli_anything_setup displayed a permission request. Choosing once was accepted, but the tool returned a second escalation and setup did not execute. Status remained available=false.", "Fix the local_execution grant path so the approved retry receives the one-shot grant."), ("F-02 | Suggested severity: High | uv-created venv has no pip", "Setup selected uv to create the managed environment. The resulting interpreter had no pip module, while install/update/uninstall invoke python -m pip. The first Draw.io install failed with No module named pip.", "Use uv pip for managed package operations or seed pip during venv creation. Add a clean-runtime install test with uv present."), ("F-03 | Suggested severity: Medium | Private HOME hides Git configuration", "Agent8088 sets HOME to the managed state directory. The user global Git HTTP/1.1 setting did not reach the pip subprocess. Manual configuration inside the private state directory was required.", "Preserve or explicitly pass required Git transport configuration into the managed subprocess environment."), ("F-04 | Suggested severity: Medium | Draw.io skill documents the wrong project option", "The packaged skill instructed project info -p project.json. The actual CLI rejected -p; the working syntax was global --project project.json before the project command.", "Update SKILL.md and examples. Add a smoke test for documented examples."), ("F-05 | Suggested severity: Medium | Missing project returns raw traceback", "A missing project produced a full Python traceback ending in FileNotFoundError. Agent8088 stayed alive and reported exit status 1, but the output was noisy and leaked implementation details.", "Catch expected file errors and return a concise, machine-readable error. Add a negative test.")]
for title, body, fix in findings:
    story.append(KeepTogether([p(title, "FT"), p(body), p("Recommended fix: " + fix, "S"), HRFlowable(width="100%", thickness=.35, color=MID, spaceAfter=8)]))

story += [p("5. Environmental limitations", "H"), p("The first two GitHub clone attempts failed with Recv failure: Connection reset by peer. Browser access worked, and forcing Git HTTP/1.1 succeeded for a direct check. After enabling VPN and configuring HTTP/1.1 within Agent8088 private runtime, the harness installed successfully. This was treated as an environment/network blocker, not a Draw.io application defect."), p("GIMP was not tested because the required gimp executable was not installed on the Mac."), p("6. Recommended release gate", "H")]
for item in ["Fix F-01 and F-02 before calling the integration install path production-ready.", "Fix or explicitly propagate Git transport configuration under the private HOME.", "Correct the Draw.io skill example and replace raw traceback output with clean errors.", "Add regression tests for approval retry, uv environments, private Git config, documented CLI examples, and missing project files.", "Repeat the same workflow from a brand-new runtime without manual ensurepip, private Git config edits, or full-auto mode."]:
    story.append(p("- " + item))
story += [p("7. Artifacts generated during the test", "H")]
for item in ["architecture.json - project with two shapes and one connector", "architecture.svg - 13,040 bytes", "architecture.png - 16,099 bytes, visually inspected", "architecture copy.svg - 13,404 bytes, path-with-spaces test"]:
    story.append(p("- " + item))
story += [p("Artifacts were created under the disposable temporary workspace rooted at /private/var/folders/.../tmp.GUfDC1oi0x/workspace.", "S"), p("Appendix: representative commands", "H"), Preformatted('/tool cli_anything_setup {}\n/tool cli_anything_search {"query":"drawio"}\n/tool cli_anything_install {"name":"drawio"}\n/tool cli_anything_skill {"name":"drawio"}\n/tool cli_anything_run {"name":"drawio","arguments":["--help"],"cwd":"<workspace>"}\n/tool cli_anything_update {"name":"drawio"}\n/tool cli_anything_uninstall {"name":"drawio"}', ss["C"])]

def footer(canvas, doc):
    canvas.saveState(); w, _ = A4; canvas.setStrokeColor(MID); canvas.setLineWidth(.5); canvas.line(18 * mm, 14 * mm, w - 18 * mm, 14 * mm); canvas.setFont("Helvetica", 7.5); canvas.setFillColor(colors.HexColor("#667085")); canvas.drawString(18 * mm, 9 * mm, "Agent8088 CLI-Anything QA report"); canvas.drawRightString(w - 18 * mm, 9 * mm, f"Page {doc.page}"); canvas.restoreState()

OUT.parent.mkdir(parents=True, exist_ok=True)
SimpleDocTemplate(str(OUT), pagesize=A4, rightMargin=18 * mm, leftMargin=18 * mm, topMargin=16 * mm, bottomMargin=20 * mm, title="Agent8088 CLI-Anything Draw.io Findings Report", author="Agent8088 QA").build(story, onFirstPage=footer, onLaterPages=footer)
print(OUT)
