# Terminal AI Agent Document Handling — Research Report

> **Research date:** 2026-08-21
> **Scope:** How terminal-based AI agents (Hermes Agent, OpenClaw) handle Word, Excel, and PDF files — whether they use code generation, LibreOffice, or the Windows Microsoft engine. Includes OfficeCLI as an alternative approach, with skill references and documentation links.

---

## Documentation Links

| Resource | URL |
|----------|-----|
| **Hermes Agent Docs (main)** | https://hermes-agent.nousresearch.com/docs |
| **Hermes — Tools & Toolsets** | https://hermes-agent.nousresearch.com/docs/user-guide/features/tools |
| **Hermes — Document Extraction** | https://hermes-agent.nousresearch.com/docs/user-guide/features/document-extraction |
| **Hermes — Skills System** | https://hermes-agent.nousresearch.com/docs/user-guide/features/skills |
| **Hermes — Bundled Skills Catalog** | https://hermes-agent.nousresearch.com/docs/reference/skills-catalog |
| **Hermes — GitHub** | https://github.com/NousResearch/hermes-agent |
| **OpenClaw Docs (main)** | https://docs.openclaw.ai/ |
| **OpenClaw — Capabilities Overview** | https://docs.openclaw.ai/capabilities |
| **OpenClaw — PDF Tool** | https://docs.openclaw.ai/tools/pdf |
| **OpenClaw — Media Overview** | https://docs.openclaw.ai/tools/media-overview |
| **OpenClaw — Skills** | https://docs.openclaw.ai/tools/skills |
| **OpenClaw — GitHub** | https://github.com/openclaw/openclaw |
| **OfficeCLI — GitHub** | https://github.com/iOfficeAI/OfficeCLI |
| **OfficeCLI — Website** | https://officecli.ai |
| **OfficeCLI — SKILL.md (for agents)** | https://officecli.ai/SKILL.md |
| **MarkItDown — GitHub** | https://github.com/microsoft/markitdown |

---

## 1. Executive Summary

**Neither Hermes nor OpenClaw uses the Windows Microsoft Office engine** to create or read Word, Excel, or PDF files. Both use **code generation** — the agent writes and executes Python/JavaScript code using open-source libraries. The key difference is in the **rendering and verification layer**:

- **Hermes** uses **LibreOffice (`soffice`)** as a system dependency for Excel formula recalculation, document rendering, and visual verification.
- **OpenClaw** uses **`clawpdf` (PDFium WebAssembly)** for PDF extraction and does NOT use LibreOffice at all. It has no built-in document creation or rendering capability.

**OfficeCLI** is a third alternative — a purpose-built CLI tool for AI agents that handles Word, Excel, and PowerPoint creation/editing with a built-in HTML rendering engine, eliminating the need for LibreOffice.

---

## 2. LibreOffice Platform Support

### Hermes Agent — LibreOffice is a system dependency

| Platform | LibreOffice support | Install command | Sandbox handling |
|----------|--------------------|-----------------|-----------------|
| **Linux** | ✅ Yes | `sudo apt install -y libreoffice` | C socket shim (`lo_socket_shim.so`) compiled on the fly if `AF_UNIX` sockets are blocked (Docker, Modal, Singularity) |
| **macOS** | ✅ Yes | `brew install libreoffice` | `gtimeout` used if available |
| **Windows (native)** | ✅ Yes | LibreOffice installed separately | No socket shim needed |
| **WSL2** | ✅ Yes | Same as Linux | Same shim logic |
| **Docker** | ✅ Must install in image | `apt install libreoffice` in Dockerfile | Wrapper + shim handles blocked `AF_UNIX` sockets |
| **Modal (serverless)** | ✅ Must install in image | Install in Modal image | Same shim logic |
| **SSH backend** | ✅ Must be on remote host | Install on remote host | Same shim logic |
| **Termux (Android)** | ⚠️ No libreoffice package available | N/A | N/A |

### OpenClaw — Does NOT use LibreOffice

| Platform | LibreOffice needed? | What it uses instead |
|----------|--------------------|--------------------|
| **All platforms** | ❌ No | `clawpdf` (PDFium WebAssembly) — runs anywhere JS runs, no system deps |
| **All platforms** | ❌ No | Native provider API (Anthropic/Google) — raw PDF bytes sent to LLM |

---

## 3. Skills Each Agent Uses for Document Files

### Hermes Agent — Document-Related Skills

Hermes uses a **skill system** where each skill is a `SKILL.md` file with instructions, prerequisites, helper scripts, and pitfalls. The agent loads the relevant skill when it detects a document task, then writes and executes code following the skill's instructions.

| File Type | Skill Name | Skill Path | Doc Link | Key Libraries | Uses LibreOffice? |
|-----------|-----------|------------|----------|--------------|-------------------|
| **Word (.docx)** | `docx` | `productivity/docx/` | [Bundled Skills Catalog](https://hermes-agent.nousresearch.com/docs/reference/skills-catalog) | `docx` (docx-js, npm), `python-docx`, `pandoc` | ✅ Yes — rendering + legacy conversion |
| **Excel (.xlsx)** | `xlsx` | `productivity/xlsx/` | [Bundled Skills Catalog](https://hermes-agent.nousresearch.com/docs/reference/skills-catalog) | `openpyxl`, `pandas`, `markitdown[xlsx]` | ✅ Yes — formula recalculation |
| **PDF (create)** | `pdf` | `productivity/pdf/` | [Bundled Skills Catalog](https://hermes-agent.nousresearch.com/docs/reference/skills-catalog) | `pypdf`, `pdfplumber`, `reportlab`, `qpdf`, `pdftotext` | ❌ No (pure Python) |
| **PDF (authoring)** | `pdf-authoring` | `productivity/pdf-authoring/` | [Bundled Skills Catalog](https://hermes-agent.nousresearch.com/docs/reference/skills-catalog) | `reportlab`, `pymupdf`, `docx2pdf` (Windows only) | ⚠️ Only `docx2pdf` on Windows (MS Word COM) |
| **PDF (edit text)** | `nano-pdf` | `productivity/nano-pdf/` | [Bundled Skills Catalog](https://hermes-agent.nousresearch.com/docs/reference/skills-catalog) | `nano-pdf` CLI | ❌ No |
| **PowerPoint (.pptx)** | `powerpoint` | `productivity/powerpoint/` | [Bundled Skills Catalog](https://hermes-agent.nousresearch.com/docs/reference/skills-catalog) | `python-pptx`, `pptxgenjs` | ✅ Yes — rendering + legacy conversion |
| **PDF/Scans (OCR)** | `ocr-and-documents` | `productivity/ocr-and-documents/` | [Bundled Skills Catalog](https://hermes-agent.nousresearch.com/docs/reference/skills-catalog) | `pymupdf`, `marker-pdf`, `pytesseract` | ❌ No (pure Python) |

**Hermes skill helper scripts (LibreOffice-dependent):**

| Script | Location | Purpose | LibreOffice? |
|--------|----------|---------|-------------|
| `soffice.py` | `productivity/docx/scripts/office/soffice.py` | Wrapper for `soffice --headless` — handles sandbox socket issues, temp profiles, C shim | ✅ Yes |
| `soffice.py` | `productivity/xlsx/scripts/office/soffice.py` | Same wrapper (shared) | ✅ Yes |
| `soffice.py` | `productivity/powerpoint/scripts/office/soffice.py` | Same wrapper (shared) | ✅ Yes |
| `recalc.py` | `productivity/xlsx/scripts/recalc.py` | Excel formula recalculation via LibreOffice + StarBasic macro | ✅ Yes |
| `accept_changes.py` | `productivity/docx/scripts/accept_changes.py` | Accept tracked changes (LibreOffice joins paragraphs correctly) | ✅ Yes |
| `merge_runs.py` | `productivity/docx/scripts/merge_runs.py` | Coalesce fragmented XML runs for find/replace | ❌ No (pure Python) |
| `validate.py` | `productivity/docx/scripts/office/validate.py` | XSD schema validation for .docx | ❌ No (pure Python) |

### OpenClaw — Document-Related Skills

OpenClaw has a **skills system** similar to Hermes. However, from the GitHub source tree, OpenClaw has far fewer document-specific skills:

| File Type | Skill/Tool Name | Source | Doc Link | Key Libraries | Uses LibreOffice? |
|-----------|----------------|--------|----------|--------------|-------------------|
| **PDF (read)** | `pdf` tool | `src/agents/tools/pdf-tool.ts` | [PDF Tool Docs](https://docs.openclaw.ai/tools/pdf) | `clawpdf` (PDFium WASM), native provider API (Anthropic/Google) | ❌ No |
| **PDF (edit text)** | `nano-pdf` skill | `skills/nano-pdf/SKILL.md` | [OpenClaw GitHub](https://github.com/openclaw/openclaw/tree/main/skills/nano-pdf) | `nano-pdf` CLI | ❌ No |
| **Document extraction** | `document-extract` plugin | `extensions/document-extract/` | [Document Extract Docs](https://docs.openclaw.ai/tools/pdf) | `clawpdf` (PDFium WASM) | ❌ No |
| **Word (.docx)** | No dedicated skill | N/A | N/A | Agent writes code via `exec`/`code_execution` | ❌ No |
| **Excel (.xlsx)** | No dedicated skill | N/A | N/A | Agent writes code via `exec`/`code_execution` | ❌ No |
| **PowerPoint (.pptx)** | No dedicated skill | N/A | N/A | Agent writes code via `exec`/`code_execution` | ❌ No |
| **PDF (create)** | No dedicated skill | N/A | N/A | Agent writes code via `exec`/`code_execution` | ❌ No |

**Key observation:** OpenClaw has NO built-in document creation skills for Word, Excel, or PowerPoint. All document creation happens through the agent writing code via `exec`/`code_execution` tools, using whatever libraries are available on the system.

---

## 4. File-by-File Method Comparison

### Word (.docx)

| Operation | Hermes | OpenClaw | OfficeCLI |
|-----------|--------|----------|-----------|
| **Read** | `read_file` tool — built-in stdlib converter → markdown | `clawpdf` does NOT handle .docx; uses `exec` tool + library | `officecli get file.docx '/paragraph[1]' --json` |
| **Create** | `docx` (docx-js, npm) — agent writes JavaScript | Agent writes code via `exec` (`python-docx`) | `officecli create doc.docx` + `officecli add doc.docx / --type paragraph ...` |
| **Edit** | Unzip → edit `word/document.xml` → re-zip | Same approach via exec tool | `officecli set doc.docx '/paragraph[1]' --prop text="New text"` |
| **Legacy .doc → .docx** | `soffice --headless --convert-to docx file.doc` | N/A | N/A |
| **Render to PDF** | `soffice --convert-to pdf` → `pdftoppm` → `vision_analyze` | N/A | `officecli view doc.docx screenshot -o /tmp/doc.png` |
| **Track changes** | `accept_changes.py` (LibreOffice) + manual XML | N/A | `officecli set doc.docx '/revision' --prop type=ins --prop author=Alice` |
| **Template merge** | N/A — agent regenerates each doc | N/A | `officecli merge template.docx out.docx --data '{"name":"Acme"}'` |
| **Tables** | docx-js `Table` with dual widths | Via exec tool | `officecli add doc.docx / --type table --prop rows=3 --prop cols=2` |
| **Images** | `ImageRun` in docx-js | Via exec tool | `officecli add doc.docx '/paragraph[1]' --type picture --prop src=image.png` |

### Excel (.xlsx)

| Operation | Hermes | OpenClaw | OfficeCLI |
|-----------|--------|----------|-----------|
| **Read** | `read_file` tool — built-in stdlib → markdown; `markitdown file.xlsx` | `exec` tool + `openpyxl` | `officecli get file.xlsx '/Sheet1!A1' --json` |
| **Create** | `openpyxl` — agent writes Python | Agent writes code via exec | `officecli create sheet.xlsx` + `officecli add sheet.xlsx '/Sheet1' --type cell --prop ref=A1 --prop value=Hello` |
| **Formulas** | `openpyxl` writes formula strings (no cached values) | Same via exec | `officecli add sheet.xlsx '/Sheet1!B1' --type formula --prop formula='=SUM(A1:A10)'` — **auto-evaluated on write** |
| **Formula recalc** | **`soffice` + StarBasic macro** (`recalc.py`) | N/A — no recalc | **Built-in 350+ function engine** — no external tool needed |
| **Recalc verification** | Re-opens with `openpyxl(data_only=True)`, scans for errors | N/A | Built-in — `get` returns computed value |
| **Charts** | `openpyxl.chart` | Via exec | `officecli add sheet.xlsx '/Sheet1' --type chart --prop chartType=bar --prop data='A1:B10'` |
| **Pivot tables** | Not supported in base skill | N/A | `officecli add sheet.xlsx '/Sheet1' --type pivottable --prop source='Data!A1:E100' --prop rows='Region' --prop values='Revenue:sum'` |
| **Conditional formatting** | `openpyxl.formatting` | Via exec | `officecli set sheet.xlsx '/Sheet1!A1:A10' --prop conditionalFormatting='...'` |
| **External link risk** | `recalc.py` refuses if external links would be destroyed | N/A | N/A — formulas evaluated in-process |

### PDF

| Operation | Hermes | OpenClaw | OfficeCLI |
|-----------|--------|----------|-----------|
| **Read (text-based)** | `read_file` → `firecrawl-anydoc` (optional, auto-installed) | `pdf` tool → `clawpdf` (PDFium WASM) extracts text | N/A (OfficeCLI doesn't read PDF) |
| **Read (scanned/OCR)** | `pymupdf` or `marker-pdf` (3-5GB models) | `pdf` tool → if text <200 chars, `clawpdf` renders pages to PNG → vision model | N/A |
| **Read (native LLM)** | N/A | ✅ `pdf` tool → native provider mode (Anthropic/Google) — raw bytes to LLM API | N/A |
| **Create** | `reportlab` (Python) — agent writes code | Agent writes code via exec | N/A (OfficeCLI creates docx/xlsx/pptx, not PDF directly) |
| **Merge/split** | `pypdf` or `qpdf` CLI | Via exec tool | N/A |
| **Edit text** | `nano-pdf` CLI (natural-language edits) | `nano-pdf` skill (same CLI) | N/A |
| **Form filling** | `pypdf` for AcroForm; annotation overlay for flat forms | Via exec tool | N/A |
| **Extract tables** | `pdfplumber` | `clawpdf` text extraction | N/A |
| **Password handling** | `pypdf` (`PdfReader(path, password=...)`) | `clawpdf` `engine.open(input, {password})` | N/A |
| **Visual verification** | `pdftoppm -jpeg` → `vision_analyze` | PNG → vision model | N/A |
| **Size limits** | 50 MB max | 10 MB per PDF (configurable), max 20 pages | N/A |
| **Sandbox support** | File bytes transferred host-side | WASM runs in-process | N/A |

### PowerPoint (.pptx)

| Operation | Hermes | OpenClaw | OfficeCLI |
|-----------|--------|----------|-----------|
| **Read** | `read_file` → `firecrawl-anydoc` (optional) | N/A | `officecli get deck.pptx '/slide[1]' --json` |
| **Create** | `python-pptx` — agent writes Python | Agent writes code via exec | `officecli create deck.pptx` + `officecli add deck.pptx / --type slide --prop title="Q4 Report"` |
| **Edit** | `python-pptx` | Via exec tool | `officecli set deck.pptx '/slide[1]/shape[1]' --prop text="New title"` |
| **Render to PDF** | `soffice --convert-to pdf` → images → `vision_analyze` | N/A | `officecli view deck.pptx screenshot -o /tmp/deck.png` |
| **Visual QA** | `soffice` → PDF → `pdftoppm` → `vision_analyze` | N/A | `officecli watch deck.pptx` — live browser preview with auto-refresh |
| **Legacy .ppt → .pptx** | `soffice --convert-to pptx file.ppt` | N/A | N/A |
| **Animations** | Not supported | N/A | `officecli set deck.pptx '/slide[1]/shape[1]' --prop animation="emphasis:fadeIn"` |
| **Transitions** | Not supported | N/A | `officecli set deck.pptx '/slide[1]' --prop transition="morph"` |
| **Round-trip dump** | N/A | N/A | `officecli dump existing.pptx -o blueprint.json` → learn from existing docs |

---

## 5. Architecture — How Each Agent Processes Documents

### Hermes Agent Document Pipeline

```
INBOUND (Reading documents)
┌──────────────────────────────────────────────────────────────┐
│ User drops file or references path                            │
│         ↓                                                      │
│  read_file tool (built-in)                                     │
│  ├─ .docx/.xlsx/.ipynb → built-in stdlib converter → markdown │
│  ├─ .pdf → firecrawl-anydoc (optional, auto-installed)        │
│  ├─ .doc/.ppt/.xls → firecrawl-anydoc                          │
│  └─ .odt/.ods/.odp/.rtf/.epub → firecrawl-anydoc               │
│         ↓                                                      │
│  Markdown/text → paginated via offset/limit                   │
│  Scanned PDFs → coverage warning + fallback to vision OCR     │
│                                                               │
│  Skills used: ocr-and-documents, pdf (for heavy extraction)    │
│  Docs: https://hermes-agent.nousresearch.com/docs/            │
│        user-guide/features/document-extraction                │
└──────────────────────────────────────────────────────────────┘

OUTBOUND (Creating documents)
┌──────────────────────────────────────────────────────────────┐
│ Agent loads relevant skill (docx, xlsx, pdf, powerpoint)      │
│  Skills docs: https://hermes-agent.nousresearch.com/docs/     │
│               reference/skills-catalog                         │
│         ↓                                                      │
│  Agent WRITES code using skill instructions:                   │
│  ├─ Word: docx-js (npm) or python-docx                        │
│  ├─ Excel: openpyxl + pandas                                  │
│  ├─ PDF: reportlab                                            │
│  └─ PPT: python-pptx                                          │
│         ↓                                                      │
│  Agent EXECUTES code via terminal/execute_code                 │
│         ↓                                                      │
│  File created on disk                                          │
│         ↓                                                      │
│  VERIFICATION (LibreOffice-dependent):                        │
│  ├─ Excel recalc: soffice + StarBasic macro (recalc.py)       │
│  ├─ Render: soffice --convert-to pdf                          │
│  ├─ Images: pdftoppm -jpeg -r 100                             │
│  └─ Visual check: vision_analyze on each page                 │
│                                                               │
│  Helper: scripts/office/soffice.py (wrapper handles sandbox)  │
└──────────────────────────────────────────────────────────────┘
```

### OpenClaw Document Pipeline

```
INBOUND (Reading — PDF only, built-in)
┌──────────────────────────────────────────────────────────────┐
│ User sends PDF via chat or references path                    │
│         ↓                                                      │
│  pdf tool resolves a PDF-capable model:                       │
│  1. agents.defaults.pdfModel                                  │
│  2. agents.defaults.imageModel                                │
│  3. Session model (if native PDF support)                     │
│  4. Auto-detected vision providers                            │
│         ↓                                                      │
│  MODE 1 — Native (Anthropic/Google):                          │
│  Raw PDF bytes → provider API as document part                │
│  Model reads PDF directly, no local extraction                │
│  Docs: https://docs.openclaw.ai/tools/pdf                     │
│         ↓                                                      │
│  MODE 2 — Extraction fallback (all other providers):          │
│  clawpdf (PDFium WASM) extracts text                          │
│  If text < 200 chars → render pages to PNG                    │
│  4M pixel budget shared across pages                          │
│  Send text + images to model                                  │
│                                                               │
│  Plugin: document-extract (extensions/document-extract/)      │
│  Dependency: clawpdf 0.3.0 (PDFium WebAssembly)               │
│  Docs: https://docs.openclaw.ai/tools/pdf                    │
└──────────────────────────────────────────────────────────────┘

OUTBOUND (Creating — no built-in, code generation only)
┌──────────────────────────────────────────────────────────────┐
│ Agent uses skills (instruction packs) to learn how            │
│  Skills docs: https://docs.openclaw.ai/tools/skills           │
│         ↓                                                      │
│  Agent writes code via exec/code_execution tools:             │
│  ├─ Word: python-docx (or any library)                       │
│  ├─ Excel: openpyxl                                           │
│  ├─ PDF: reportlab                                            │
│  └─ PPT: python-pptx                                         │
│         ↓                                                      │
│  No formula recalc (no LibreOffice)                           │
│  No visual verification (no rendering engine)                 │
│  No legacy format support                                     │
│                                                               │
│  Only document skill: nano-pdf (PDF text editing)             │
│  Source: https://github.com/openclaw/openclaw/tree/           │
│          main/skills/nano-pdf                                 │
└──────────────────────────────────────────────────────────────┘
```

### OfficeCLI Approach (Alternative)

```
INBOUND (Reading)
┌──────────────────────────────────────────────────────────────┐
│ officecli get file.docx '/paragraph[1]' --json               │
│ officecli view file.pptx outline                              │
│ officecli view file.docx html -o /tmp/preview.html            │
└──────────────────────────────────────────────────────────────┘

OUTBOUND (Creating)
┌──────────────────────────────────────────────────────────────┐
│ officecli create deck.pptx                                   │
│ officecli add deck.pptx / --type slide --prop title="Report" │
│ officecli add deck.pptx '/slide[1]' --type shape \           │
│   --prop text="Revenue grew 25%" --prop x=2cm --prop y=5cm  │
│                                                               │
│ Template merge (design once, fill many):                     │
│ officecli merge template.docx out.docx \                     │
│   --data '{"client":"Acme","total":"$5,200"}'                │
│                                                               │
│ Round-trip learning (learn from existing docs):               │
│ officecli dump existing.docx -o blueprint.json              │
│ officecli batch blueprint.json new.docx                      │
└──────────────────────────────────────────────────────────────┘

VERIFICATION (Built-in, no LibreOffice needed)
┌──────────────────────────────────────────────────────────────┐
│ officecli view deck.pptx screenshot -o /tmp/deck.png         │
│ officecli watch deck.pptx  # live preview at localhost:26315 │
│                                                               │
│ Built-in HTML rendering engine — no Office, no LibreOffice   │
│ Per-page PNG screenshots ready for multimodal agents         │
│ Excel formulas auto-evaluated on write (350+ functions)     │
└──────────────────────────────────────────────────────────────┘

INSTALLATION
┌──────────────────────────────────────────────────────────────┐
│ macOS/Linux: curl -fsSL https://raw.githubusercontent.com/    │
│              iOfficeAI/OfficeCLI/main/install.sh | bash       │
│ Windows:    irm https://raw.githubusercontent.com/            │
│              iOfficeAI/OfficeCLI/main/install.ps1 | iex       │
│ Homebrew:   brew install officecli                           │
│ npm:        npm install -g @officecli/officecli              │
│ Scoop:      scoop install officecli                          │
│                                                               │
│ Agent SKILL.md: curl -fsSL https://officecli.ai/SKILL.md    │
│ GitHub:      https://github.com/iOfficeAI/OfficeCLI          │
└──────────────────────────────────────────────────────────────┘
```

---

## 6. Key Differences Summary

| Aspect | Hermes | OpenClaw | OfficeCLI |
|--------|--------|----------|-----------|
| **LibreOffice dependency** | ✅ Yes — system-level | ❌ No | ❌ No |
| **PDF extraction engine** | `firecrawl-anydoc` (optional) | `clawpdf` (PDFium WASM, bundled) | N/A |
| **Native LLM PDF reading** | ❌ No | ✅ Yes (Anthropic, Google) | N/A |
| **Excel formula recalc** | ✅ LibreOffice + StarBasic macro | ❌ No | ✅ Built-in 350+ function engine |
| **Visual verification** | ✅ LibreOffice → PDF → images → vision | ❌ No | ✅ Built-in HTML renderer → PNG |
| **Legacy format conversion** | ✅ LibreOffice (.doc→.docx, .ppt→.pptx) | ❌ No | ❌ No |
| **Sandbox-safe rendering** | C socket shim for soffice | WASM (no system deps) | Single binary (embedded .NET) |
| **Document creation** | Code generation (skills teach libraries) | Code generation (skills teach libraries) | CLI commands (purpose-built) |
| **Windows MS engine** | Only `docx2pdf` (Word COM) niche fallback | ❌ No | ❌ No |
| **Max PDF size** | 50 MB | 10 MB (configurable) | N/A |
| **Max PDF pages** | No hard limit (paginated) | 20 (configurable) | N/A |
| **Template merge** | ❌ No (agent regenerates each doc) | ❌ No | ✅ `merge` — fill `{{key}}` placeholders |
| **Round-trip learning** | ❌ No | ❌ No | ✅ `dump` → `batch` — learn from existing docs |
| **Pivot tables** | ❌ Not in base skill | ❌ No | ✅ Native OOXML pivot generation |
| **Live preview** | ❌ No | ❌ No | ✅ `watch` — auto-refreshing browser preview |
| **Single binary** | ❌ No (multiple libraries) | ❌ No (Node.js + npm) | ✅ Yes (self-contained) |
| **Cross-platform** | ✅ Linux, macOS, Windows, WSL2, Docker, Modal, SSH | ✅ Any platform with Node 22+ | ✅ macOS, Linux, Windows |
| **License** | MIT | MIT | Apache 2.0 |
| **Docs** | [hermes-agent.nousresearch.com](https://hermes-agent.nousresearch.com/docs) | [docs.openclaw.ai](https://docs.openclaw.ai/) | [officecli.ai](https://officecli.ai) |

---

## 7. The `soffice.py` Wrapper — Engineering Detail (Hermes Only)

Hermes wraps `soffice` because bare `soffice` **hangs in sandboxed environments** (Docker, Modal, SSH backends). The wrapper (`scripts/office/soffice.py`) does the following:

1. **Sets `SAL_USE_VCLPLUGIN=svp`** — headless VCL plugin
2. **Creates a temp `UserInstallation` profile per call** — avoids profile conflicts when multiple calls happen
3. **Detects if `AF_UNIX` sockets are blocked** — tries `socket.socket(AF_UNIX, SOCK_STREAM)`; if it fails, compiles a **C shim** on the fly
4. **The C shim (`lo_socket_shim.so`)** intercepts `socket()`, `listen()`, `accept()`, `close()` system calls to make LibreOffice work in sandboxes where Unix domain sockets are restricted

This wrapper is shared across the `docx`, `xlsx`, and `powerpoint` skills — each has a copy at `scripts/office/soffice.py`.

### Excel Recalculation Flow (Hermes)

```
openpyxl writes formulas as strings with NO cached values
    ↓
recalc.py creates a temp LibreOffice profile
    ↓
Installs a StarBasic macro (RecalculateAndSave):
    ThisComponent.calculateAll()
    ThisComponent.store()
    ThisComponent.close(True)
    ↓
soffice --headless --norestore + macro + file path
    ↓
File rewritten in place with computed values
    ↓
Re-open with openpyxl(data_only=True)
    ↓
Scan for #VALUE!, #NAME?, #REF!, #DIV/0!, #NULL!, #NUM!, #N/A
    ↓
Return JSON: {status: "success", total_formulas: 42, total_errors: 0}
```

**Formula limitations:** LibreOffice cannot evaluate `XLOOKUP`, `XMATCH`, `SORT`, `FILTER`, `UNIQUE`, `SEQUENCE` (spilling array functions). The xlsx skill instructs the agent to use `INDEX`/`MATCH` instead and to sort/filter in Python before writing cells.

---

## 8. Recommendation for Your Terminal Agent

### Option A: Hermes-style (LibreOffice-based)

**Best if:** You need full Excel formula recalculation, visual verification, and legacy format support on Linux/macOS.

```
INBOUND:  read_file + firecrawl-anydoc (PDF, legacy Office)
OUTBOUND: Code generation (openpyxl, reportlab, python-docx, python-pptx)
RECALC:   soffice + StarBasic macro
VERIFY:   soffice --convert-to pdf → pdftoppm → vision_analyze
```

**Pros:** Full formula recalc, visual verification loop, legacy format support, battle-tested.
**Cons:** LibreOffice must be installed (~500MB), sandbox complexity (C shim), slower (process spawn per recalc/render).

### Option B: OpenClaw-style (WASM, no system deps)

**Best if:** You want zero system dependencies, run in containers/sandbox frequently, and mostly need to READ PDFs.

```
INBOUND:  clawpdf (PDFium WASM) for PDF text/image extraction
          Native API (Anthropic/Google) for direct PDF reading
OUTBOUND: Code generation only (no built-in document tools)
VERIFY:   None (agent is "blind" to output)
```

**Pros:** Zero system deps, works anywhere, fast PDF extraction, native LLM PDF input.
**Cons:** No formula recalc, no visual verification, no legacy formats, no document creation tools.

### Option C: OfficeCLI-based (purpose-built for agents)

**Best if:** You want the agent to create and edit Word/Excel/PPT with visual feedback, without LibreOffice.

```
INBOUND:  officecli get/view (structured JSON, outline, HTML)
OUTBOUND: officecli create/add/set/remove (CLI commands)
RECALC:   Built-in 350+ function engine (no external tool)
VERIFY:   officecli view screenshot (built-in PNG rendering)
TEMPLATES: officecli merge (design once, fill many)
LEARNING: officecli dump/batch (learn from existing docs)
```

**Pros:** Single binary, built-in rendering engine, auto-evaluated formulas, template merge, round-trip dump, live preview.
**Cons:** No PDF creation (only docx/xlsx/pptx), external dependency, CLI-based (not pure code generation).

### Option D: Hybrid (Recommended)

**Best if:** You want maximum flexibility and the best of all approaches.

```
INBOUND (reading documents):
  PDF:       clawpdf (WASM) or pymupdf (lightweight)
  Scanned:   marker-pdf or vision OCR
  Anthropic: Send raw bytes to API (native PDF input)
  Word/Excel: read_file stdlib converter → markdown

OUTBOUND (creating documents):
  Word/Excel/PPT: OfficeCLI (visual feedback, formulas, templates)
  PDF:            reportlab (pure Python, no system deps)
  Legacy:         soffice --convert-to (if LibreOffice available)

VERIFICATION:
  OfficeCLI: officecli view screenshot (built-in, no deps)
  Fallback:   soffice → pdftoppm → vision (if LibreOffice available)

FORMULA RECALC:
  Primary:  OfficeCLI built-in engine (350+ functions, auto-evaluated)
  Fallback: soffice + StarBasic macro (if OfficeCLI unavailable)
```

### Implementation priority:

1. **Start with OfficeCLI** for Word/Excel/PPT creation — single binary, built-in rendering, no system deps
2. **Add `pymupdf`/`clawpdf`** for PDF reading — lightweight, works in sandboxes
3. **Add `reportlab`** for PDF creation — pure Python, no system deps
4. **Add LibreOffice as optional** — if available, use for legacy format conversion and as fallback recalc/render
5. **Add `nano-pdf`** for natural-language PDF text edits
6. **Add `marker-pdf`** for OCR-heavy scanned documents

This gives you a portable agent that works everywhere out of the box, with optional enhanced capabilities when LibreOffice is present.