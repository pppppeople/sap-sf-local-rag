"""Private source records and a small, evidence-labelled SAP wiki.

This module is never called by the public retriever. All data lives below the
configured private notes directory, not below the application checkout.
"""
from __future__ import annotations

import json
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4
import yaml

TOPICS = {
    "邮件触发": {
        "aliases": ["邮件", "邮件触发", "email", "notification", "通知", "post save"],
        "terms": "Onboarding Trigger Email Notification Process Post Save Rule",
        "notes": ["email-trigger", "onboarding"],
        "related": ["业务规则", "流程对象", "邮件模板"],
        "body": """## 核心区分

模板配置、规则挂载、业务事件实际发生和邮件发送结果，需要分别确认。

## 排查顺序

1. 记录流程 ID、实际状态变化和发生时间。
2. 回查官方来源中的 Trigger Email Notification 场景及 Process 对象规则要求。
3. 对照租户检查 Email Category、Email Trigger、Email Template、收件人和规则条件。
4. 使用新触发的流程保留实际结果；模板保存或规则截图不能代替发送证据。

## 证据状态

历史笔记曾记录新员工资料审核阶段邮件未触发，仍需具体租户结果验证。这里是排查组织方式，不声明已经找到唯一原因。""",
    },
    "自定义任务默认值": {
        "aliases": ["默认值", "default value", "initialize", "初始化", "placeholder", "picklist"],
        "terms": "Onboarding Custom Task MDF Initialize Default Value Picklist",
        "notes": ["onboarding-custom-task-default-value", "custom-field-onboarding-task"],
        "related": ["业务规则", "MDF对象", "选项列表"],
        "body": """## 核心区分

静态默认值、初始化规则和界面占位文字，应分别记录。显示提示不等于字段已获得存储值。

## 配置前确认

- 明确字段类型、目标 MDF 对象和真实任务实例。
- 回查官方来源中该类型是否支持 Default Value，以及 Initialize 的场景、事件与挂载位置。
- 区分新建实例与已有实例，分别观察生成值和保存值。

## 证据状态

历史笔记包含 Picklist 默认值和初始化规则方案，但“所有字段类型都支持”、Save Rules 挂载位置及覆盖顺序未在此次初始化中重新验证。不要直接把这些旧断言作为官方结论。""",
    },
    "任务可见性": {
        "aliases": ["可见性", "visibility", "assigned to others", "my tasks", "dashboard", "看不到", "无权限"],
        "terms": "Onboarding Dashboard Task Visibility Role Based Permissions",
        "notes": ["onboarding-task-visibility"],
        "related": ["角色权限", "目标人群", "MDF对象"],
        "body": """## 核心区分

任务条目是否显示、状态列是否显示、记录能否打开、任务能否执行，是四种不同结果。

## 排查顺序

1. 使用具体比较用户记录页面入口、角色、目标员工和任务类型。
2. 回查官方来源中的任务对象权限、目标人群与 Dashboard 状态权限。
3. 分别记录查看和编辑的实际结果，不用“角色已分配”替代访问验证。
4. 对历史笔记中的 Prepare for New Team Members 入口效果做实际比较，不预先承诺隐藏 Assigned to Others。

## 证据状态

旧笔记提出对象权限、列权限及访问入口三种处理，未保存足够证据证明它们在当前租户全部生效。""",
    },
}
SUPPORT = {
    "业务规则": ("规则场景、基础对象、触发事件和挂载位置共同限定规则上下文。具体字段和动作必须回查官方原文。", ["邮件触发", "自定义任务默认值"]),
    "流程对象": ("Process 对象与已生成的流程实例需要区分。状态变化和保存事件必须使用实际流程验证。", ["邮件触发"]),
    "邮件模板": ("模板承载邮件内容和变量。模板、触发条件与收件人是不同的检查对象。", ["邮件触发"]),
    "MDF对象": ("对象定义、业务记录、界面和权限是相互关联的不同层次。不要把界面变化当作后端数据变化。", ["自定义任务默认值", "任务可见性"]),
    "选项列表": ("选项标签、内部代码与排序值需要区分。赋值及比较采用哪个值应以字段和规则场景的原文说明为准。", ["自定义任务默认值"]),
    "角色权限": ("获得权限的用户、权限项与权限作用的目标范围需要分别核对。", ["任务可见性"]),
    "目标人群": ("目标人群限定权限作用的人员或记录范围，不等同于获得角色的权限组成员。", ["任务可见性"]),
}
EXTRA_PAGES = {
    "默认值方案对比": ("comparisons", ["自定义任务默认值"], """## 比较维度

| 方案 | 需要回查的配置层 | 核查重点 |
| --- | --- | --- |
| 静态默认值 | MDF 字段定义 | 该字段类型是否提供 Default Value，实际新建值与保存值 |
| 初始化规则 | 业务规则与对象事件 | 场景、基础对象、Initialize 目的和正确挂载位置 |
| 占位文字 | 界面控件属性 | 文字是否仅显示为提示，字段是否实际产生数据 |

这是历史笔记的比较框架。字段支持范围、执行顺序和保存行为必须逐项回查官方摘录与租户结果，不能因表格整理而视为已验证。关联：[[自定义任务默认值]]、[[业务规则]]、[[MDF对象]]。"""),
    "任务显示与访问权限": ("comparisons", ["任务可见性"], """## 比较维度

| 观察对象 | 实际验证方式 |
| --- | --- |
| 任务条目 | 比较用户在同一入口是否看到条目 |
| 状态列 | 比较对应任务类型的状态列是否显示 |
| 打开记录 | 记录点击后的实际结果与错误 |
| 执行任务 | 记录能否编辑、提交和完成 |

上述观察不能相互替代。旧笔记提出的入口、对象权限和列权限方案仍需分别核验。关联：[[任务可见性]]、[[角色权限]]、[[目标人群]]。"""),
    "SAP SuccessFactors Onboarding": ("entities", list(TOPICS), """## 主题入口

[[邮件触发]]、[[自定义任务默认值]]、[[任务可见性]]。

本页连接私人案例与相关概念，不声明租户配置已成功。使用前区分 Onboarding 1.0 与当前 Onboarding，再核对资料版本与实际实例。"""),
}
TOPICS["自定义任务默认值"]["related"].append("默认值方案对比")
TOPICS["任务可见性"]["related"].append("任务显示与访问权限")
for spec in TOPICS.values():
    spec["related"].append("SAP SuccessFactors Onboarding")
OFFICIAL = {
    "邮件触发": ["Onboarding / Onboarding_2.0", "Platform / SF_PLT_Managing_Email_Notifications"],
    "自定义任务默认值": ["Onboarding / Onboarding_2.0", "Platform / SF_IMP_MDF", "Platform / SF_PLT_Implementing_Business_Rules"],
    "任务可见性": ["Onboarding / Onboarding_2.0", "Platform / SF_RBP_Impl"],
}
SCHEMA = """# 私人 SAP 知识库

raw 保存完整原始问答和笔记快照，只创建一次，不合并、不摘要、不覆盖。
wiki 保存中文概念、实体和对比页，可以持续更新。DOCX 是既有交付格式。

所有 wiki 页面含 title、confidence、last_ingested、sources、stale。
还应保留 evidence_status、product_version、verification、aliases、retrieval_terms。
sources 使用本知识库内的 raw 相对路径。内部链接采用 [[文件名]]。

confidence 不等于租户验证。historical-unverified / unverified 记录只能作为
经验线索；model-reviewed 只表示回答模型审核，不表示租户验证。stale 为 true 的
内容不得直接复用。文档版本变化后复核依赖页，不自动将旧结论改成新事实。

先查 wiki，再回查 raw 案例和官方检索摘录。私人案例不进入官方 sources，
也不向公共机器人或固定朋友账号发布。没有官方摘录时明确说明证据缺口。

新记录更新关联页的来源和案例索引，不能自动改写为已验证结论。
极短或尚未解决的记录保持原状态，不强制拆成固定数量的页面。
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def outside_starter_scope(text: str) -> bool:
    return bool(re.search(r"(?<![a-z])(?:learning|recruiting|compensation|payroll|succession)(?![a-z])|学习|薪酬|继任|(?:onboarding|onb)\s*1\.0", text, re.I))


def frontmatter(metadata: dict, body: str) -> str:
    return "---\n" + yaml.safe_dump(metadata, allow_unicode=True, sort_keys=False) + "---\n\n" + body.rstrip() + "\n"


def read_page(path: Path) -> tuple[dict, str]:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        raise ValueError(f"Missing frontmatter: {path.name}")
    header, body = text[4:].split("\n---\n", 1)
    return yaml.safe_load(header), body.strip()


def extract_docx(path: Path) -> str:
    from docx import Document
    from docx.oxml.ns import qn
    doc = Document(path)
    # Preserve paragraph/table order instead of appending every table at the end.
    lines = []
    for element in doc.element.body:
        if element.tag == qn("w:p"):
            lines.append("".join(t.text or "" for t in element.iter(qn("w:t"))))
        elif element.tag == qn("w:tbl"):
            for row in element.iter(qn("w:tr")):
                cells = ["".join(t.text or "" for t in c.iter(qn("w:t"))) for c in row if c.tag == qn("w:tc")]
                lines.append(" | ".join(cells))
    return "\n\n".join(lines)


class KnowledgeStore:
    def __init__(self, notes_dir: Path):
        self.root = Path(notes_dir) / "knowledge"

    def layout(self) -> None:
        for folder in ("raw/articles", "raw/papers", "raw/notes", "raw/assets", "wiki/concepts", "wiki/entities", "wiki/comparisons"):
            (self.root / folder).mkdir(parents=True, exist_ok=True)
        schema = self.root / "CLAUDE.md"
        if not schema.exists():
            schema.write_text(SCHEMA, encoding="utf-8")
        log = self.root / "wiki/log.md"
        if not log.exists():
            log.write_text(frontmatter(self.metadata("知识库操作日志"), "# 知识库操作日志"), encoding="utf-8")

    def metadata(self, title: str, sources: list | None = None, **extra) -> dict:
        return {"title": title, "confidence": 0.55, "last_ingested": datetime.now().date().isoformat(),
                "sources": sources or [], "stale": False, "evidence_status": "historical-unverified",
                "product_version": "未确认", "verification": "未进行租户验证", **extra}

    def log(self, message: str) -> None:
        with (self.root / "wiki/log.md").open("a", encoding="utf-8") as stream:
            stream.write(f"\n- {now()}：{message}\n")

    def index(self) -> None:
        pages = self.pages()
        sources = sorted({s for _, m, _ in pages for s in m.get("sources", [])})
        body = "# 全局目录\n\n私人经验用于提供线索，产品事实仍回查官方原文。\n\n"
        for directory in ("concepts", "entities", "comparisons"):
            body += "## " + {"concepts": "概念", "entities": "实体", "comparisons": "对比"}[directory] + "\n\n"
            for path, meta, _ in pages:
                if path.parent.name == directory:
                    body += f"- [[{path.stem}|{meta['title']}]]（{meta['evidence_status']}，{'待复核' if meta['stale'] else '未标记过期'}）\n"
            body += "\n"
        (self.root / "wiki/index.md").write_text(frontmatter(self.metadata("全局目录", sources), body), encoding="utf-8")

    def pages(self) -> list:
        wiki = self.root / "wiki"
        return [(p, *read_page(p)) for d in ("concepts", "entities", "comparisons") for p in sorted((wiki / d).glob("*.md"))]

    def seed(self) -> None:
        self.layout()
        for title, spec in TOPICS.items():
            path = self.root / "wiki/concepts" / f"{title}.md"
            if not path.exists():
                body = f"# {title}\n\n{spec['body']}\n\n## 关联概念\n\n" + "、".join(f"[[{n}]]" for n in spec["related"]) + "\n\n## 原始记录\n"
                path.write_text(frontmatter(self.metadata(title, aliases=spec["aliases"], retrieval_terms=spec["terms"]), body), encoding="utf-8")
        for title, (description, related) in SUPPORT.items():
            path = self.root / "wiki/concepts" / f"{title}.md"
            if not path.exists():
                body = f"# {title}\n\n{description}\n\n关联：" + "、".join(f"[[{n}]]" for n in related) + "。\n"
                path.write_text(frontmatter(self.metadata(title, aliases=[title], retrieval_terms=""), body), encoding="utf-8")
        for title, (folder, topics, description) in EXTRA_PAGES.items():
            path = self.root / "wiki" / folder / f"{title}.md"
            if not path.exists():
                path.write_text(frontmatter(self.metadata(title, aliases=[title], retrieval_terms="", related_topics=topics),
                    f"# {title}\n\n{description}\n\n## 原始记录\n"), encoding="utf-8")
        self.index()

    def attach(self, title: str, source: str, label: str) -> None:
        folder = EXTRA_PAGES[title][0] if title in EXTRA_PAGES else "concepts"
        path = self.root / "wiki" / folder / f"{title}.md"
        meta, body = read_page(path)
        if source in meta["sources"]:
            return
        meta["sources"].append(source)
        meta["last_ingested"] = datetime.now().date().isoformat()
        # A new unverified record must not clear stale or upgrade verification.
        body += f"\n- [{label}](../../{source})：原始记录，未重新核验。\n"
        path.write_text(frontmatter(meta, body), encoding="utf-8")

    def record(self, question: str, answer: str, sources: list, *, title: str = "SAP 原始问答",
               previous_note: Path | None = None, update_topics: bool = True,
               model_reviewed: bool = False) -> Path:
        self.seed()
        record_id = datetime.now().strftime("%Y%m%dT%H%M%S") + "-" + uuid4().hex[:12]
        relative = f"raw/notes/{record_id}.md"
        body = f"# {title}\n\n## 原始输入\n\n{question}\n\n## 原始回答或整理输入\n\n{answer}\n\n## 当时记录的来源\n\n" + "\n".join(f"- {s if isinstance(s, str) else json.dumps(s, ensure_ascii=False)}" for s in sources)
        if previous_note and previous_note.exists():
            snapshot = self.root / "raw/notes" / f"{record_id}-before.docx"
            with snapshot.open("xb") as dest, previous_note.open("rb") as src:
                shutil.copyfileobj(src, dest)
            body += f"\n\n合并前笔记原件：[{previous_note.name}]({snapshot.name})\n"
        target = self.root / relative
        with target.open("x", encoding="utf-8") as stream:
            stream.write(frontmatter(self.metadata(title,
                evidence_status="model-reviewed" if model_reviewed else "unverified",
                recorded_at=now()), body))
        matched = self.match_topics(question + "\n" + title)
        if update_topics:
            for name in matched:
                self.attach(name, relative, title)
                for linked in TOPICS[name]["related"]:
                    self.attach(linked, relative, title)
            if not matched:
                # A new case still gets a durable, searchable page without inventing facts.
                name = "记录-" + record_id
                p = self.root / "wiki/entities" / f"{name}.md"
                p.write_text(frontmatter(self.metadata(title, [relative], evidence_status="unverified", aliases=[title], retrieval_terms=question),
                    f"# {title}\n\n尚未整理为已验证结论。\n\n原始问题：{question}\n\n[完整记录](../../{relative})"), encoding="utf-8")
        self.log(f"保存独立原始记录 {record_id}；关联主题：{'、'.join(matched) or '独立案例'}；未自动提升证据状态。")
        self.index()
        return target

    @staticmethod
    def match_topics(text: str) -> list[str]:
        low = text.lower()
        if outside_starter_scope(low):
            return []
        return [title for title, spec in TOPICS.items() if any(alias in low for alias in spec["aliases"])]

    def bootstrap(self, notes_dir: Path, docs_dir: Path | None = None, manifest: Path | None = None) -> dict:
        self.seed()
        imported = 0
        # Only the established note locations; never recursively import knowledge snapshots.
        notes = sorted(Path(notes_dir).glob("*.docx")) + sorted((Path(notes_dir) / "daily-capture").glob("*.docx"))
        for note in notes:
            if note.name.startswith("~$"):
                continue
            rel = Path("raw/notes/legacy") / note.relative_to(notes_dir)
            dest = self.root / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            if not dest.exists():
                with dest.open("xb") as a, note.open("rb") as b:
                    shutil.copyfileobj(b, a)
                # Read the frozen copy, never a later modified live note.
                extracted = self.root / rel.with_suffix(".md")
                extracted.write_text(frontmatter(self.metadata(note.stem, evidence_status="historical-unverified"),
                    f"# {note.stem}\n\n原件：[{note.name}]({note.name})\n\n" + extract_docx(dest)), encoding="utf-8")
                imported += 1
            source = rel.with_suffix(".md").as_posix()
            if not (self.root / source).exists():
                # Interrupted extraction can be completed from the already retained original.
                (self.root / source).write_text(frontmatter(self.metadata(note.stem), extract_docx(dest)), encoding="utf-8")
            for title, spec in TOPICS.items():
                if note.stem in spec["notes"]:
                    self.attach(title, source, note.stem)
                    for linked in spec["related"]:
                        self.attach(linked, source, note.stem)
        if docs_dir:
            wanted = {s.split(" / ")[-1] for refs in OFFICIAL.values() for s in refs}
            for original in sorted(Path(docs_dir).rglob("*.pdf")):
                if original.stem not in wanted:
                    continue
                dest = self.root / "raw/papers" / original.name
                if not dest.exists():
                    with dest.open("xb") as a, original.open("rb") as b:
                        shutil.copyfileobj(b, a)
                for title, refs in OFFICIAL.items():
                    if any(ref.endswith(" / " + original.stem) for ref in refs):
                        self.attach(title, dest.relative_to(self.root).as_posix(), original.name)
                        for linked in TOPICS[title]["related"]:
                            self.attach(linked, dest.relative_to(self.root).as_posix(), original.name)
        if manifest:
            self.check_sources(manifest, bind=True)
        self.index()
        self.log(f"初始化三个主题；新增 {imported} 份旧笔记原件快照；旧 DOCX 与原索引未覆盖。")
        return {"root": str(self.root), "imported": imported, "pages": len(self.pages())}

    def check_sources(self, manifest: Path, bind: bool = False) -> list:
        documents = {d["source"]: d for d in json.loads(Path(manifest).read_text(encoding="utf-8"))["documents"]}
        changed = []
        for path, meta, body in self.pages():
            refs = OFFICIAL.get(meta["title"], [])
            if meta["title"] in SUPPORT:
                refs = list(dict.fromkeys(s for title in SUPPORT[meta["title"]][1] for s in OFFICIAL[title]))
            if meta.get("related_topics"):
                refs = list(dict.fromkeys(s for title in meta["related_topics"] for s in OFFICIAL[title]))
            baseline = meta.get("official_dependencies", [])
            if bind and not baseline:
                meta["official_dependencies"] = [{"source": ref,
                    "documentVersion": documents[ref].get("documentVersion"),
                    "publishedDate": documents[ref].get("publishedDate")} for ref in refs if ref in documents]
                versions = sorted({d["documentVersion"] for d in meta["official_dependencies"] if d.get("documentVersion")})
                meta["product_version"] = " / ".join(versions) or "未确认"
                path.write_text(frontmatter(meta, body), encoding="utf-8")
                continue
            stale_refs = []
            for old in baseline:
                current = documents.get(old["source"])
                if not current or current.get("status") in {"excluded", "retired", "obsolete"} or any(current.get(k) != old.get(k) for k in ("documentVersion", "publishedDate")):
                    stale_refs.append(old["source"])
            if stale_refs and not meta["stale"]:
                meta["stale"] = True
                meta["review_reason"] = "来源缺失、停用或版本/发布日期发生变化：" + "、".join(stale_refs)
                path.write_text(frontmatter(meta, body), encoding="utf-8")
                changed.append(meta["title"])
        if changed:
            self.log("来源变化，标记待复核页面：" + "、".join(changed))
            self.index()
        return changed

    def retrieve(self, question: str, limit: int = 3, manifest: Path | None = None) -> dict:
        if not (self.root / "wiki").exists():
            return {"pages": [], "context": "", "retrieval_terms": ""}
        if manifest and Path(manifest).exists():
            self.check_sources(manifest)
        from lib.sf_tokenize import tokenize_query
        tokens = set(tokenize_query(question))
        ranked = []
        other_module = outside_starter_scope(question)
        for path, meta, body in self.pages():
            if other_module and meta["title"] in {*TOPICS, *SUPPORT, *EXTRA_PAGES}:
                continue
            if meta.get("stale"):
                continue
            aliases = [meta["title"]] + meta.get("aliases", [])
            direct = sum(1 for alias in aliases if alias.lower() in question.lower())
            terms = set(tokenize_query(meta["title"] + " " + meta.get("retrieval_terms", "") + " " + " ".join(meta.get("aliases", []))))
            overlap = tokens & terms
            # Single broad words such as 'task' must not select an unrelated topic.
            broad = {"sap", "successfactors", "onboarding", "task", "custom", "configuration", "configure", "object", "mdf", "data", "field", "rule", "process"}
            if not direct and (len(overlap) < 2 or not (overlap - broad)):
                continue
            ranked.append((direct * 10 + len(overlap), path, meta, body))
        ranked.sort(key=lambda x: x[0], reverse=True)
        selected = ranked[:limit]
        pages = [{"title": m["title"], "path": str(p.relative_to(self.root)), "kind": "private_wiki",
                  "evidenceStatus": m["evidence_status"], "productVersion": m["product_version"],
                  "verification": m["verification"], "sources": m["sources"]} for _, p, m, _ in selected]
        sections = [f"### {m['title']}\n状态：{m['evidence_status']}；版本：{m['product_version']}；{m['verification']}\n{b[:1600]}" for _, p, m, b in selected]
        # Read the retained raw source. This is evidence context, not an official citation.
        seen = set()
        for _, _, meta, _ in selected:
            for relative in reversed(meta["sources"]):
                if not relative.endswith(".md"):
                    continue
                if relative in seen or len(seen) >= 3:
                    continue
                seen.add(relative)
                source = self.root / relative
                if source.suffix == ".md" and source.exists():
                    _, body = read_page(source)
                    quoted = "\n".join("> " + line for line in body[:1800].splitlines())
                    sections.append(f"### 原始案例 {source.stem}（私人历史记录，未核验）\n{quoted}")
        context = ("## 私人中文知识页与原始案例\n\n只作为排查线索，不是 SAP 官方事实。下面的官方摘录才可支持产品结论。\n\n" + "\n\n".join(sections))[:6500] if selected else ""
        terms = " ".join(dict.fromkeys(m.get("retrieval_terms", "") for _, _, m, _ in selected if m.get("retrieval_terms")))
        return {"pages": pages, "context": context, "retrieval_terms": terms[:250]}

    def validate(self) -> list[str]:
        """Validate the stored graph before deployment or after manual wiki edits."""
        pages = self.pages()
        stems = {p.stem for p, _, _ in pages} | {"index", "log"}
        errors = []
        paths = [p for p, _, _ in pages] + [self.root / "wiki/index.md", self.root / "wiki/log.md"]
        for path in paths:
            meta, body = read_page(path)
            for key in ("title", "confidence", "last_ingested", "sources", "stale"):
                if key not in meta:
                    errors.append(f"{path.name}: missing {key}")
            for source in meta.get("sources", []):
                if not (self.root / source).is_file():
                    errors.append(f"{path.name}: missing source {source}")
            for link in re.findall(r"\[\[([^\]]+)\]\]", body):
                if link.split("|", 1)[0].split("#", 1)[0] not in stems:
                    errors.append(f"{path.name}: missing link {link}")
        return errors


def combine_context(official: str, private: dict) -> str:
    """Keep official evidence inside consumers' existing 14k context budget."""
    if not private["context"]:
        return official
    instruction = ("私人部分不能支持肯定式产品结论。需要引用其判断时，逐项明确写“历史笔记记录（需验证）”；"
                   "未被官方摘录直接支持的配置步骤、字段限制和行为不得写成已确认事实。\n\n")
    return instruction + private["context"][:4800] + "\n\n## 回查官方原文摘录\n\n" + official[:8700]


def official_query(question: str, private: dict) -> str:
    # Only maintained public topic terms, never case bodies or user-edited aliases.
    for page in private["pages"]:
        if page["title"] in TOPICS:
            return question + " " + TOPICS[page["title"]]["terms"]
    return question


def enrich_payload(payload: dict, private: dict) -> dict:
    # Official sources retain their existing meaning for citation/verifier consumers.
    return {**payload, "knowledgeSources": private["pages"],
            "formatted": combine_context(payload["formatted"], private)}
