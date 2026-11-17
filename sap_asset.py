"""Small asset helpers for SAP SF notes.

This script keeps the "consultant asset" workflow lightweight:
- update notes/INDEX.md from existing docx notes
- capture a short project lesson into a structured docx note
- turn project material into interview/portfolio wording
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from uuid import uuid4
from datetime import datetime, timedelta
from pathlib import Path

from docx import Document
from knowledge import KnowledgeStore, enrich_payload, official_query

from ask import NOTES_DIR, build_context, cross_module_enrich, load_index, search, write_claude_note


PROJECT_DIR = Path(__file__).parent
INDEX_FILE = NOTES_DIR / "INDEX.md"
ASSET_REPORT_FILE = NOTES_DIR / "ASSET_REPORT.md"
METADATA_DIR = NOTES_DIR / "metadata"
DAILY_CAPTURE_DIR = NOTES_DIR / "daily-capture"

CATEGORY_RULES = [
    ("Onboarding", ["onboarding", "onboardee", "welcome", "hire", "new-hire", "compliance", "task", "panel", "process"]),
    ("RBP / Permission", ["rbp", "permission", "role", "target", "population", "security"]),
    ("Workflow / Rule", ["workflow", "rule", "trigger", "approval", "validate"]),
    ("MDF / UI / Data", ["mdf", "manage", "configuration", "ui", "field", "data", "retention", "globalinfo"]),
    ("Email / Notification", ["email", "notification", "template"]),
    ("Offboarding / Contingent", ["offboarding", "contingent", "worker"]),
]


def slugify(text: str, fallback: str = "sap-note") -> str:
    text = text.lower()
    replacements = {
        "权限": "permission",
        "工作流": "workflow",
        "规则": "rule",
        "入职": "onboarding",
        "离职": "offboarding",
        "邮件": "email",
        "字段": "field",
        "配置": "configuration",
        "流程": "process",
    }
    for src, dst in replacements.items():
        text = text.replace(src, f" {dst} ")
    words = re.findall(r"[a-z0-9]+", text)
    stop = {"the", "and", "for", "with", "from", "this", "that", "into", "today", "issue", "problem", "note"}
    useful = [word for word in words if len(word) > 1 and word not in stop]
    return "-".join(useful[:7]) or fallback


def category_for(text: str) -> str:
    haystack = text.lower()
    for category, keys in CATEGORY_RULES:
        if any(key in haystack for key in keys):
            return category
    return "General"


def tags_for(text: str) -> list[str]:
    haystack = text.lower()
    tags = []
    for category, keys in CATEGORY_RULES:
        if any(key in haystack for key in keys):
            tags.append(category)
    if any(key in haystack for key in ["error", "issue", "not visible", "看不到", "没有触发", "失败", "排查"]):
        tags.append("Troubleshooting")
    return tags or ["General"]


def case_type_for(text: str) -> str:
    haystack = text.lower()
    if any(key in haystack for key in ["看不到", "没有触发", "失败", "报错", "排查", "visibility", "not visible", "error"]):
        return "troubleshooting"
    if any(key in haystack for key in ["workflow", "rule", "trigger", "approval", "规则", "工作流"]):
        return "workflow-rule"
    if any(key in haystack for key in ["rbp", "permission", "role", "target population", "权限"]):
        return "permission"
    if any(key in haystack for key in ["email", "notification", "邮件", "通知"]):
        return "notification"
    if any(key in haystack for key in ["mdf", "field", "object", "字段", "对象"]):
        return "configuration"
    return "configuration"


def difficulty_for(text: str) -> str:
    haystack = text.lower()
    score = 0
    for key in ["integration", "workflow", "business rule", "rbp", "target population", "cross", "global", "rule", "approval", "external user", "non-standard", "非标", "跨", "集成", "权限", "规则", "排查"]:
        if key in haystack:
            score += 1
    if score >= 4:
        return "advanced"
    if score >= 2:
        return "intermediate"
    return "basic"


def note_title(path: Path) -> str:
    try:
        doc = Document(path)
        for para in doc.paragraphs:
            text = para.text.strip()
            if text:
                return text[:90]
    except Exception:
        pass
    return path.stem.replace("-", " ").title()


def note_preview(path: Path) -> str:
    try:
        doc = Document(path)
        parts = []
        for para in doc.paragraphs:
            text = para.text.strip()
            if text and text != note_title(path):
                parts.append(text)
            if len(" ".join(parts)) > 180:
                break
        return " ".join(parts)[:220]
    except Exception:
        return ""


def update_index() -> Path:
    NOTES_DIR.mkdir(exist_ok=True)
    notes = sorted(
        [p for p in NOTES_DIR.rglob("*.docx") if not p.name.startswith("~$")],
        key=lambda p: str(p.relative_to(NOTES_DIR)).lower(),
    )
    groups: dict[str, list[Path]] = {}
    for path in notes:
        title = note_title(path)
        preview = note_preview(path)
        category = category_for(f"{path.stem} {title} {preview}")
        groups.setdefault(category, []).append(path)
        write_metadata(path, title, category, tags_for(f"{path.stem} {title} {preview}"), preview)

    lines = [
        "# SAP SF Notes Index",
        "",
        f"Last updated: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        "",
        "This index is generated from `notes/*.docx` for quick retrieval and portfolio/case review.",
        "",
    ]
    ordered_categories = [name for name, _ in CATEGORY_RULES] + ["General"]
    for category in ordered_categories:
        items = groups.get(category, [])
        if not items:
            continue
        lines.append(f"## {category}")
        lines.append("")
        for path in items:
            title = note_title(path)
            preview = note_preview(path)
            meta_rel = f"metadata/{path.stem}.md"
            rel = str(path.relative_to(NOTES_DIR)).replace(" ", "%20")
            suffix = f" - {preview}" if preview else ""
            lines.append(f"- [{title}]({rel}) ([meta]({meta_rel})) `{path.stem}`{suffix}")
        lines.append("")

    INDEX_FILE.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return INDEX_FILE


def write_metadata(path: Path, title: str, category: str, tags: list[str], preview: str) -> Path:
    METADATA_DIR.mkdir(exist_ok=True)
    rel = path.relative_to(NOTES_DIR)
    meta_path = METADATA_DIR / f"{path.stem}.md"
    updated = datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
    tag_text = ", ".join(dict.fromkeys(tags))
    combined = f"{path.stem} {title} {preview}"
    content = f"""# {title}

- file: `{rel}`
- category: {category}
- tags: {tag_text}
- difficulty: {difficulty_for(combined)}
- case_type: {case_type_for(combined)}
- decision_point: 待补充
- reuse_for: project-reference, interview, report, portfolio
- updated: {updated}

## Preview

{preview or "No preview extracted."}

## Case Structure Checklist

- 问题摘要
- 适用场景
- 配置路径
- 排查思路
- 最终方案
- 面试/汇报说法

## Decision Point

待补充：当时为什么选这个方案？有没有替代方案？风险是什么？
"""
    meta_path.write_text(content, encoding="utf-8")
    return meta_path


def build_capture_markdown(raw: str) -> tuple[str, str, str]:
    first_line = next((line.strip() for line in raw.splitlines() if line.strip()), raw.strip())
    title = first_line[:42] or "SAP SF Project Capture"
    slug = slugify(first_line)
    content = f"""## 问题摘要

{raw.strip()}

## 适用场景

- SAP SuccessFactors 项目执行中的配置、排查或沟通沉淀
- 后续可补充模块、配置路径、截图和最终处理结果

## 排查/处理记录

1. 现象或需求：{first_line}
2. 已知上下文：待补充项目模块、对象、角色、流程节点或报错信息
3. 下一步：补充配置路径、验证步骤和最终结论

## 可复用结论

- 把这个问题沉淀为可复用案例，后续遇到同类配置或沟通场景时优先检索本笔记。

## 待补充

- 配置路径
- 截图位置
- 相关权限/对象/规则
- 最终验证结果
"""
    return slug, title, content


def capture(text: str, markdown: str | None = None, daily: bool = False) -> Path:
    slug, title, fallback_md = build_capture_markdown(text)
    directory = DAILY_CAPTURE_DIR if daily else NOTES_DIR
    target = directory / f"{slug}.docx"
    if daily and target.exists():
        slug += "-" + datetime.now().strftime('%Y%m%d%H%M%S') + "-" + uuid4().hex[:6]
        target = directory / f"{slug}.docx"
    # Retain the user's full input before DOCX creation or daily-path movement.
    KnowledgeStore(NOTES_DIR).record(text, markdown or fallback_md, [], title=title, previous_note=target)
    path = write_claude_note(slug, title, markdown or fallback_md, record=False, directory=directory)
    update_index()
    return path


def interviewize(text: str) -> str:
    clean = text.strip()
    return f"""## 面试表达草稿

### 简历 bullet
- 参与 SAP SuccessFactors 相关配置与问题排查，围绕需求理解、配置验证、问题定位和文档沉淀形成可复用交付资产。

### STAR 案例
**S（情境）**：在 SAP SuccessFactors 项目执行中，遇到如下配置/流程问题：{clean}

**T（任务）**：需要理解业务场景，定位相关配置对象、权限或流程节点，并给出可验证的处理方案。

**A（行动）**：我先把问题拆成业务现象、系统配置、权限/规则影响和验证路径几部分；再查阅知识库与既有项目笔记，整理排查步骤，最后将结论沉淀为可复用文档。

**R（结果）**：问题被转化为可复盘的配置案例，后续同类场景可以更快检索、复用和对外解释，也能作为 Onboarding/SF 项目经验的面试素材。

### 可继续补强
- 加入具体模块名、配置路径和业务影响
- 加入量化结果，例如节省排查时间、减少返工、提升交付一致性
- 去除客户或项目敏感信息后，可整理成作品集案例
"""


def recent_notes(limit: int = 8) -> list[Path]:
    NOTES_DIR.mkdir(exist_ok=True)
    notes = [p for p in NOTES_DIR.rglob("*.docx") if not p.name.startswith("~$")]
    return sorted(notes, key=lambda p: p.stat().st_mtime, reverse=True)[:limit]


def workbench() -> str:
    update_index()
    notes = recent_notes()
    lines = [
        "## SAP 项目工作台",
        "",
        f"- 笔记总数：{len([p for p in NOTES_DIR.rglob('*.docx') if not p.name.startswith('~$')])}",
        f"- 索引文件：{INDEX_FILE}",
        "",
        "### 最近更新的笔记",
    ]
    if not notes:
        lines.append("- 暂无笔记")
    for path in notes:
        when = datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
        lines.append(f"- {when}｜{note_title(path)}｜`{path.name}`")
    lines.extend([
        "",
        "### 今天建议",
        "- 只记录真实项目里遇到的一个问题或一个配置路径。",
        "- 优先补齐：问题背景、配置路径、验证结果、可复用结论。",
        "- 如果已经有处理过程，可以用“扣子面试化”转成 STAR 或简历表达。",
    ])
    return "\n".join(lines)


def weekly_review() -> str:
    DAILY_CAPTURE_DIR.mkdir(exist_ok=True)
    notes = sorted(
        [p for p in DAILY_CAPTURE_DIR.glob("*.docx") if not p.name.startswith("~$")],
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    lines = [
        "## Weekly SAP Capture Review",
        "",
        f"Daily captures: {len(notes)}",
        "",
    ]
    if not notes:
        lines.extend([
            "本周还没有 daily-capture。",
            "",
            "今天可以先用：",
            "扣子沉淀：",
            "模块：",
            "现象：",
            "配置路径：",
            "我怎么判断：",
            "最后结论：",
            "待补充：",
        ])
        return "\n".join(lines)

    grouped: dict[str, list[Path]] = {}
    for path in notes:
        text = f"{path.stem} {note_title(path)} {note_preview(path)}"
        grouped.setdefault(category_for(text), []).append(path)

    for category, items in grouped.items():
        lines.append(f"### {category}")
        for path in items[:8]:
            when = datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d")
            preview = note_preview(path)
            lines.append(f"- {when}｜{note_title(path)}｜`daily-capture/{path.name}`")
            if preview:
                lines.append(f"  - {preview[:180]}")
        lines.append("")

    lines.extend([
        "### 建议整理动作",
        "- 挑 1-2 条最像真实项目案例的 daily capture，补齐配置路径、判断依据和最终结论。",
        "- 用 `扣子记录坑：...` 把高价值 daily capture 提炼成正式 note。",
        "- 用 `扣子面试化：...` 或 `扣子作品集：...` 转成可展示表达。",
    ])
    return "\n".join(lines)


def asset_report(days: int = 7, write_file: bool = True) -> str:
    update_index()
    cutoff = datetime.now() - timedelta(days=days)
    all_notes = sorted(
        [p for p in NOTES_DIR.rglob("*.docx") if not p.name.startswith("~$")],
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    recent = [p for p in all_notes if datetime.fromtimestamp(p.stat().st_mtime) >= cutoff]
    daily = [p for p in all_notes if DAILY_CAPTURE_DIR in p.parents]
    formal = [p for p in all_notes if DAILY_CAPTURE_DIR not in p.parents]

    grouped: dict[str, list[Path]] = {}
    for path in recent:
        text = f"{path.stem} {note_title(path)} {note_preview(path)}"
        grouped.setdefault(category_for(text), []).append(path)

    candidates = []
    for path in recent:
        text = f"{path.stem} {note_title(path)} {note_preview(path)}"
        score = 0
        if path in daily:
            score += 1
        if difficulty_for(text) in {"intermediate", "advanced"}:
            score += 1
        if case_type_for(text) in {"troubleshooting", "permission", "workflow-rule"}:
            score += 1
        if any(key in text.lower() for key in ["onboarding", "rbp", "workflow", "rule", "permission", "target"]):
            score += 1
        if score >= 2:
            candidates.append((score, path))
    candidates = [path for _score, path in sorted(candidates, key=lambda item: (-item[0], item[1].stat().st_mtime))][:8]

    lines = [
        "# SAP Asset Report",
        "",
        f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        f"Window: last {days} days",
        "",
        "## Snapshot",
        "",
        f"- Total notes: {len(all_notes)}",
        f"- Formal notes: {len(formal)}",
        f"- Daily captures: {len(daily)}",
        f"- Recently changed: {len(recent)}",
        f"- Metadata files: {len(list(METADATA_DIR.glob('*.md'))) if METADATA_DIR.exists() else 0}",
        "",
        "## Recent Notes By Module",
        "",
    ]
    if not recent:
        lines.append("- No recent notes in this window.")
    else:
        ordered_categories = [name for name, _ in CATEGORY_RULES] + ["General"]
        for category in ordered_categories:
            items = grouped.get(category, [])
            if not items:
                continue
            lines.append(f"### {category}")
            for path in items[:10]:
                when = datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d")
                bucket = "daily" if path in daily else "formal"
                preview = note_preview(path)
                rel = path.relative_to(NOTES_DIR)
                lines.append(f"- {when}｜{bucket}｜{note_title(path)}｜`{rel}`")
                if preview:
                    lines.append(f"  - {preview[:180]}")
            lines.append("")

    lines.extend([
        "## Upgrade Candidates",
        "",
    ])
    if not candidates:
        lines.extend([
            "- No obvious upgrade candidates yet.",
            "- Next action: add one daily capture with module, phenomenon, config path, judgment, and final result.",
        ])
    else:
        for path in candidates:
            text = f"{path.stem} {note_title(path)} {note_preview(path)}"
            rel = path.relative_to(NOTES_DIR)
            lines.append(f"- {note_title(path)}｜`{rel}`")
            lines.append(f"  - module: {category_for(text)}")
            lines.append(f"  - case_type: {case_type_for(text)}")
            lines.append(f"  - next: 补齐配置路径、判断依据、最终结论，再转成面试/作品集表达")

    lines.extend([
        "",
        "## Recommended Next Actions",
        "",
        "1. Pick one daily capture and promote it with `扣子记录坑：...`.",
        "2. Pick one permission/workflow troubleshooting item and run `扣子面试化：...`.",
        "3. Pick one reusable configuration case and run `扣子作品集：...`.",
        "4. Keep new raw captures in `notes/daily-capture/`; keep polished reusable cases as formal notes.",
    ])
    report = "\n".join(lines).rstrip() + "\n"
    if write_file:
        ASSET_REPORT_FILE.write_text(report, encoding="utf-8")
    return report


def portfolioize(text: str) -> str:
    clean = text.strip()
    return f"""## 作品集案例草稿

### 案例标题
SAP SuccessFactors 项目配置与问题排查案例

### 可公开描述
围绕 SAP SuccessFactors 项目执行中的配置、权限、流程或数据问题，完成需求理解、问题拆解、配置验证和文档沉淀，并将处理过程整理为可复用知识资产。

### 项目材料
{clean}

### 展示结构
1. 背景：说明业务场景和系统模块，不写客户敏感信息。
2. 问题：说明遇到的配置、权限、流程或数据挑战。
3. 行动：说明如何定位对象、权限、规则、流程节点或配置路径。
4. 结果：说明验证结果、复用价值和沉淀文档。
5. 反思：说明下次如何更快排查，或如何标准化。

### README/作品集表达
- Built reusable SAP SuccessFactors implementation notes from real project troubleshooting, covering configuration paths, permission checks, process validation, and consultant-facing documentation.

### 还需要补充
- 具体模块：Onboarding / RBP / Workflow / MDF / EC 等
- 配置路径和关键对象
- 处理前后差异或节省时间
- 可脱敏截图或流程图
"""


def query_context_data(question: str) -> dict:
    store = KnowledgeStore(NOTES_DIR)
    manifest_path = PROJECT_DIR / "index" / "manifest.json"
    if manifest_path.exists() and (store.root / "wiki").exists():
        store.check_sources(manifest_path)
    private = store.retrieve(question)
    search_question = official_query(question, private)
    # Retrieve only from this export's bring-your-own-documents index.
    chunks, bm25 = load_index()
    results, _tokens = search(search_question, chunks, bm25)
    extra = cross_module_enrich(search_question, results, chunks, bm25)
    if extra:
        results = results + extra
    sources = []
    for result in results[:5]:
        source = result.get("source", "")
        if source and source not in sources:
            sources.append(source)
    context = build_context(results[:8])
    lines = [
        "## SAP 知识库检索资料",
        "",
        f"问题：{question.strip()}",
        "",
        "### 来源",
    ]
    lines.extend([f"- {source}" for source in sources] or ["- 无明确匹配来源"])
    lines.extend(["", "### 摘录", context or "无匹配摘录"])
    manifest_path = PROJECT_DIR / "index" / "manifest.json"
    metadata = {}
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        metadata = {item.get("source"): item for item in manifest.get("documents", []) if item.get("source")}
    except (OSError, json.JSONDecodeError):
        pass
    return enrich_payload({
        "schemaVersion": 1,
        "question": question.strip(),
        "retrievalMode": "hybrid" if getattr(__import__('ask'), '_EMBEDDINGS', None) is not None else "bm25",
        "sources": [
            {
                "source": source,
                "sourceUrl": metadata.get(source, {}).get("sourceUrl"),
                "documentVersion": metadata.get(source, {}).get("documentVersion"),
                "publishedDate": metadata.get(source, {}).get("publishedDate"),
            }
            for source in sources
        ],
        "context": context,
        "formatted": "\n".join(lines),
    }, private)


def main() -> None:
    parser = argparse.ArgumentParser(description="SAP SF asset helper")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("index", help="update notes/INDEX.md")
    ki = sub.add_parser("knowledge-init", help="retain original notes and initialise three private wiki topics")
    ki.add_argument("--docs-dir", type=Path, default=PROJECT_DIR / "docs")
    ki.add_argument("--manifest", type=Path, default=PROJECT_DIR / "index/manifest.json")
    ks = sub.add_parser("knowledge-search", help="search private wiki and retained cases without a model")
    ks.add_argument("--text", default="-", help="question text, or - for stdin")
    kr = sub.add_parser("knowledge-check-sources", help="mark pages affected by official document version changes")
    kr.add_argument("--manifest", type=Path, default=PROJECT_DIR / "index/manifest.json")
    sub.add_parser("knowledge-check", help="validate private wiki frontmatter, sources and internal links")
    sub.add_parser("knowledge-record", help="retain one final private question/answer from JSON stdin")

    cap = sub.add_parser("capture", help="capture project lesson into notes")
    cap.add_argument("--text", default="-", help="raw capture text, or - for stdin")
    cap.add_argument("--markdown-file", default=None, help="structured markdown to write")
    cap.add_argument("--daily", action="store_true", help="write into notes/daily-capture/")

    itv = sub.add_parser("interview", help="turn text into STAR/interview wording")
    itv.add_argument("--text", default="-", help="source text, or - for stdin")

    sub.add_parser("workbench", help="show SAP project asset workbench")
    sub.add_parser("weekly", help="summarize notes/daily-capture/")
    ar = sub.add_parser("asset-report", help="generate SAP asset report")
    ar.add_argument("--days", type=int, default=7, help="lookback window in days")
    ar.add_argument("--no-write", action="store_true", help="do not write notes/ASSET_REPORT.md")

    pf = sub.add_parser("portfolio", help="turn text into portfolio case wording")
    pf.add_argument("--text", default="-", help="source text, or - for stdin")

    qc = sub.add_parser("query-context", help="retrieve SAP KB context for a question")
    qc.add_argument("--text", default="-", help="question text, or - for stdin")
    qc.add_argument("--json", action="store_true", help="emit structured retrieval data")

    args = parser.parse_args()
    if args.cmd == "knowledge-record":
        entry = json.load(sys.stdin)
        if not isinstance(entry.get("question"), str) or not isinstance(entry.get("answer"), str):
            raise ValueError("knowledge-record requires question and answer strings")
        if not entry["question"].strip() or not entry["answer"].strip():
            raise ValueError("knowledge-record requires a complete question and answer")
        path = KnowledgeStore(NOTES_DIR).record(entry["question"], entry["answer"], entry.get("sources", []),
            model_reviewed=entry.get("modelReviewed") is True)
        print(json.dumps({"ok": True, "record": str(path.relative_to(NOTES_DIR))}, ensure_ascii=False))
        return
    if args.cmd == "knowledge-init":
        print(json.dumps(KnowledgeStore(NOTES_DIR).bootstrap(NOTES_DIR, args.docs_dir,
                         args.manifest if args.manifest.exists() else None), ensure_ascii=False))
        return
    if args.cmd == "knowledge-search":
        text = sys.stdin.read() if args.text == "-" else args.text
        print(json.dumps(KnowledgeStore(NOTES_DIR).retrieve(text), ensure_ascii=False))
        return
    if args.cmd == "knowledge-check-sources":
        print(json.dumps({"stale_pages": KnowledgeStore(NOTES_DIR).check_sources(args.manifest)}, ensure_ascii=False))
        return
    if args.cmd == "knowledge-check":
        errors = KnowledgeStore(NOTES_DIR).validate()
        print(json.dumps({"ok": not errors, "errors": errors}, ensure_ascii=False))
        if errors:
            raise SystemExit(1)
        return
    if args.cmd == "index":
        print(update_index())
        return
    if args.cmd == "capture":
        text = sys.stdin.read() if args.text == "-" else args.text
        markdown = Path(args.markdown_file).read_text(encoding="utf-8") if args.markdown_file else None
        print(capture(text, markdown, daily=args.daily))
        return
    if args.cmd == "interview":
        text = sys.stdin.read() if args.text == "-" else args.text
        print(interviewize(text))
        return
    if args.cmd == "workbench":
        print(workbench())
        return
    if args.cmd == "weekly":
        print(weekly_review())
        return
    if args.cmd == "asset-report":
        print(asset_report(days=args.days, write_file=not args.no_write))
        return
    if args.cmd == "portfolio":
        text = sys.stdin.read() if args.text == "-" else args.text
        print(portfolioize(text))
        return
    if args.cmd == "query-context":
        text = sys.stdin.read() if args.text == "-" else args.text
        data = query_context_data(text)
        print(json.dumps(data, ensure_ascii=False) if args.json else data["formatted"])


if __name__ == "__main__":
    main()
