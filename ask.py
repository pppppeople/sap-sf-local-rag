"""
SAP SuccessFactors Assistant - Interactive Q&A
BM25 检索 + 多 LLM provider 支持
"""

import sys
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
import json
import os
import pickle
import re
import shutil
import subprocess
import argparse
import warnings
import threading
import time
import tempfile
from pathlib import Path
from functools import wraps
from typing import Optional
from lib.result import Result, failure, exception_result, diagnose
from knowledge import KnowledgeStore, combine_context, extract_docx, official_query

# 压掉 sentence-transformers / urllib3 的版本兼容警告
warnings.filterwarnings("ignore", category=UserWarning)
warnings.filterwarnings("ignore", message=".*NotOpenSSLWarning.*")
warnings.filterwarnings("ignore", message=".*urllib3.*")

HISTORY_FILE = Path.home() / ".sf-assistant-history"

# ── ANSI 颜色 ─────────────────────────────────────────────────────────────────
RS = "\033[0m"   # reset
BD = "\033[1m"   # bold
DM = "\033[2m"   # dim
CY = "\033[36m"  # cyan
GR = "\033[32m"  # green
YL = "\033[33m"  # yellow
RD = "\033[31m"  # red

def _w() -> int:
    """Usable terminal width, capped at 72."""
    return min(shutil.get_terminal_size().columns - 2, 72)


# ── 终端等待动画 ───────────────────────────────────────────────────────────────
class CatSpinner:
    """右侧等待状态：空闲时静态，loading 时轻量旋转。"""

    IDLE = "博丽结界待机"

    # 纯 ASCII 宽度可控，不用 emoji 避免双宽字符撑破行宽
    FRAMES = [
        "<o>   ",
        " <o>  ",
        "  <o> ",
        "   <o>",
        "  <o> ",
        " <o>  ",
    ]
    _LINE_W = 36  # 固定行宽，足够覆盖所有帧

    def __init__(self):
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._msg = ""

    def start(self, msg: str = "少女祈祷中"):
        self._msg = msg
        self._stop.clear()
        self._thread = threading.Thread(target=self._spin, daemon=True)
        self._thread.start()

    def set_msg(self, msg: str):
        self._msg = msg

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=1)
        print(f"\r{' ' * self._LINE_W}", end="\r", flush=True)

    def _spin(self):
        i = 0
        while not self._stop.is_set():
            frame = self.FRAMES[i % len(self.FRAMES)]
            text = f"  {self._msg}  {CY}{frame}{RS}"
            # pad with spaces to fixed width so previous longer frame is overwritten
            pad = max(0, self._LINE_W - len(self._msg) - len(frame) - 4)
            print(f"\r{DM}{text}{RS}{' ' * pad}", end="", flush=True)
            i += 1
            time.sleep(0.25)


_cat = CatSpinner()

def _sep() -> str:
    return f"{DM}{'─' * _w()}{RS}"

def _render_inline(text: str) -> str:
    """Render inline **bold** and `code` to ANSI."""
    text = re.sub(r'\*\*(.+?)\*\*', lambda m: f"{BD}{m.group(1)}{RS}", text)
    text = re.sub(r'`([^`]+)`', lambda m: f"{CY}{m.group(1)}{RS}", text)
    return text

def render_md(text: str) -> str:
    """Convert markdown answer to ANSI-styled terminal output."""
    lines = text.split('\n')
    out = []
    in_code_block = False

    for line in lines:
        # fenced code block
        if line.strip().startswith('```'):
            in_code_block = not in_code_block
            if in_code_block:
                out.append(f"{DM}┌{'─' * min(_w()-2, 60)}┐{RS}")
            else:
                out.append(f"{DM}└{'─' * min(_w()-2, 60)}┘{RS}")
            continue
        if in_code_block:
            out.append(f"{DM}│{RS} {CY}{line}{RS}")
            continue

        # horizontal rule
        if re.match(r'^-{3,}$', line.strip()) or re.match(r'^_{3,}$', line.strip()):
            out.append(f"{DM}{'─' * _w()}{RS}")
            continue

        # headings
        m = re.match(r'^(#{1,3})\s+(.*)', line)
        if m:
            level = len(m.group(1))
            title = _render_inline(m.group(2).strip())
            if level == 1:
                out.append(f"\n{BD}{CY}{'━' * min(len(m.group(2))+2, _w())}{RS}")
                out.append(f"{BD}{CY}  {title}{RS}")
                out.append(f"{BD}{CY}{'━' * min(len(m.group(2))+2, _w())}{RS}")
            elif level == 2:
                out.append(f"\n{BD}{CY}  {title}{RS}")
                out.append(f"{DM}  {'─' * min(len(m.group(2)), _w()-4)}{RS}")
            else:
                out.append(f"\n{BD}  {title}{RS}")
            continue

        # blockquote / Note
        m = re.match(r'^>\s*(.*)', line)
        if m:
            out.append(f"  {YL}│{RS} {DM}{_render_inline(m.group(1))}{RS}")
            continue

        # bullet
        m = re.match(r'^(\s*)[-*•]\s+(.*)', line)
        if m:
            indent = '  ' * (len(m.group(1)) // 2 + 1)
            out.append(f"{indent}{DM}·{RS} {_render_inline(m.group(2))}")
            continue

        # numbered list
        m = re.match(r'^(\s*)(\d+)\.\s+(.*)', line)
        if m:
            indent = '  ' * (len(m.group(1)) // 2 + 1)
            out.append(f"{indent}{BD}{m.group(2)}.{RS} {_render_inline(m.group(3))}")
            continue

        # blank line
        if not line.strip():
            out.append('')
            continue

        # normal paragraph
        out.append(_render_inline(line))

    # collapse 3+ consecutive blank lines to 1
    result = []
    blanks = 0
    for l in out:
        if l == '':
            blanks += 1
            if blanks <= 1:
                result.append(l)
        else:
            blanks = 0
            result.append(l)
    return '\n'.join(result).strip()


def display_answer(answer: str, smooth: bool = True) -> None:
    """Print an answer with a smoother terminal reveal."""
    rendered = render_md(answer)
    if not smooth or not sys.stdout.isatty():
        print(f"\n{rendered}\n")
        return

    print()
    line_delay = 0.045
    chunk_delay = 0.012
    for line in rendered.splitlines():
        if not line:
            print()
            time.sleep(line_delay)
            continue
        # Code/table-ish lines should appear by line to avoid awkward ANSI/code tearing.
        plain = re.sub(r'\x1b\[[0-9;]*m', '', line)
        if plain.startswith(("│", "┌", "└")) or len(plain) > 140:
            print(line)
            time.sleep(line_delay)
            continue
        for i in range(0, len(line), 6):
            print(line[i:i + 6], end="", flush=True)
            time.sleep(chunk_delay)
        print()
        time.sleep(line_delay)
    print()

PROJECT_DIR = Path(__file__).parent
WORKSPACE_DIR = PROJECT_DIR.parent
INDEX_DIR = PROJECT_DIR / "index"
NOTES_DIR = Path(
    os.environ.get("SAP_SF_NOTES_DIR", Path.home() / ".local/share/sap-sf-assistant/notes")
).expanduser()
CONFIG_PATH = Path.home() / ".sf-assistant-config.json"
SAP_DRAFT_SCRIPT = (Path(os.environ["SAP_CAPTURE_DRAFT_SCRIPT"]).expanduser()
                    if os.environ.get("SAP_CAPTURE_DRAFT_SCRIPT") else None)

def remember_sap_capture(question: str, answer: str = "", source: str = "sap-assistant") -> None:
    """Add terminal SAP questions to the shared daily capture draft pool."""
    if SAP_DRAFT_SCRIPT is None or not SAP_DRAFT_SCRIPT.exists():
        return
    try:
        subprocess.run(
            [
                "node",
                str(SAP_DRAFT_SCRIPT),
                "add",
                "--source",
                source,
                "--text",
                question,
                "--answer",
                answer[:1800],
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5,
            check=False,
        )
    except Exception:
        pass

def _load_platform_cfg() -> dict:
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8")).get("platform", {})
    except Exception:
        return {}

_PCFG = _load_platform_cfg()

def _env_cli_path(variable: str) -> Optional[str]:
    for value in os.environ.get(variable, "").split(os.pathsep):
        if value.strip():
            candidate = Path(value.strip()).expanduser()
            if candidate.exists():
                return str(candidate)
    return None

CLAUDE_BIN = _PCFG.get("claude_bin") or shutil.which("claude") or "claude"
CODEX_BIN = _PCFG.get("codex_bin") or _env_cli_path("SAP_SF_CODEX_PATH") or shutil.which("codex") or "codex"

def _find_nlm() -> str:
    # 1. Explicit environment candidates, then PATH lookup
    configured = _env_cli_path("SAP_SF_NLM_PATH")
    if configured:
        return configured
    found = shutil.which("nlm") or shutil.which("nlm.exe")
    if found:
        return found
    # 2. Common install locations
    candidates = [
        Path.home() / ".local/bin/nlm",
        Path.home() / "anaconda3/envs/ima-mcp/Scripts/nlm.exe",
        Path.home() / "miniconda3/envs/ima-mcp/Scripts/nlm.exe",
    ]
    for p in candidates:
        if p.exists():
            return str(p)
    return "nlm"

NLM_BIN = _PCFG.get("nlm_bin") or _find_nlm()

# ── NotebookLM 集成 ────────────────────────────────────────────────────────────
# 本节（_find_nlm / NLM_NOTEBOOKS / _detect_nlm_module / call_notebooklm）
# 由 Claude Code (claude-sonnet-4-6) 于 2026-05-29 生成。
# 依赖 notebooklm-mcp-cli 提供的 nlm CLI。
#
# NotebookLM notebook ID per module (can be overridden in ~/.sf-assistant-config.json)
NLM_NOTEBOOKS: dict[str, str] = {}  # Configure your own notebook IDs locally.

_NLM_MODULE_KEYWORDS: dict[str, list[str]] = {
    "onboarding":       ["onboarding", "offboarding", "onb", "入职", "离职"],
    "learning":         ["learning", "lms", "course", "curriculum", "training", "课程", "培训"],
    "recruiting":       ["recruiting", "recruitment", "rcm", "job requisition", "招聘"],
    "employee_central": ["employee central", " ec ", "position management", "time management", "employment info", "员工信息"],
    "performance":      ["performance", "goal management", "calibration", "360", "绩效", "目标"],
    "compensation":     ["compensation", "variable pay", "salary", "薪酬"],
    "succession":       ["succession", "career development", "mentoring", "继任", "职业发展"],
    "work_zone":        ["work zone", "workzone"],
    "joule":            ["joule", "ai assistant", "ai copilot"],
    "platform":         ["rbp", "mdf", "business rule", "workflow", "permission", "role-based", "platform", "权限", "业务规则"],
}

def _detect_nlm_module(question: str) -> str:
    q = question.lower()
    scores = {m: sum(1 for kw in kws if kw in q) for m, kws in _NLM_MODULE_KEYWORDS.items()}
    best = max(scores, key=scores.get)
    return best if scores[best] > 0 else "platform"

TOP_K = 15
MAX_CONTEXT_CHARS = 48000

# ── 向量检索模块级状态 ─────────────────────────────────────────────────────────
_EMBEDDINGS = None          # numpy array loaded from index/embeddings.npy, or None
_ST_MODEL   = None          # SentenceTransformer instance, pre-warmed in background
_ST_READY   = threading.Event()  # set when model is loaded AND warmed up


def _prewarm_models():
    """Background thread: pre-init jieba + load ST model + warmup encode."""
    global _ST_MODEL
    try:
        # 1. jieba 初始化（避免首次分词延迟）
        sys.path.insert(0, str(Path(__file__).parent / 'lib'))
        from sf_tokenize import tokenize_query
        tokenize_query("onboarding workflow permission")

        # 2. 加载 SentenceTransformer
        from sentence_transformers import SentenceTransformer
        model = SentenceTransformer('paraphrase-multilingual-MiniLM-L12-v2')

        # 3. warmup：触发 PyTorch JIT 编译，消除首次 encode 延迟
        model.encode(["warmup"], normalize_embeddings=True)

        _ST_MODEL = model
    except Exception:
        pass
    finally:
        _ST_READY.set()  # 无论成功与否都 set，search() 不会死等


def _get_st_model():
    """Return ST model if available (non-blocking, for callers that already waited)."""
    return _ST_MODEL

# ONB 1.0 docs — excluded by default since we work on ONB 2.0
EXCLUDED_SOURCES = {
    "Onboarding / SF_ONB_Impl",
    "Onboarding / SF_ONB_Admin_en",
    "Onboarding / SF_ONB1_Compliance_Form_Updates",
    "Onboarding / SF_ONB_Compliance_Form_Updates",
}

SYSTEM_PROMPT = """You are the user's local SAP SuccessFactors expert consultant assistant. Use the provided local documentation as the evidence base and keep personal project experience clearly separated from official product facts.

## Answer quality requirements
Every answer should be implementation-ready when the evidence supports it. This means:
- **Navigation paths**: Give an exact Admin Center path only when it is supported by the provided excerpts. Otherwise identify what must be confirmed.
- **Rule configuration**: Give exact Rule Scenario, Base Object, conditions, and actions only when the excerpts support those values. Never invent exact fields to make an answer look complete.
- **Step granularity**: Number every action. "Configure the rule" is not acceptable — write out every click, every field, every dropdown selection.
- **Cross-module steps**: If the feature involves multiple modules (e.g., Onboarding + RBP + Business Rules + EC), provide a dedicated section for each module's configuration, in dependency order.
- **Field names**: Use the exact system field names/IDs as they appear in the UI or documentation, not paraphrases.

## Language
- Answer in the same language as the question (Chinese question → Chinese answer, English → English)
- Cite only source documents that actually support the published conclusion

## Hard rules — never do these
- NEVER say "refer to the SAP Help Portal / official documentation / SAP Community". Answer directly.
- When local documentation is insufficient, say what evidence is missing. Training knowledge or the user's project experience may be added only under a clearly labeled "经验假设（需验证）" section with a concrete verification method.
- NEVER infer that an undocumented version or scenario is unsupported merely because the retrieved excerpt discusses another version.
- NEVER give a high-level summary when implementation steps are needed. Always go deep.
- NEVER defer the question back to the user. Every answer must be complete and actionable."""

# ── Provider 配置 ────────────────────────────────────────────────────────────

PROVIDERS = {
    "codex": {
        "desc": "Codex / OpenAI 本地订阅（无需 API Key）",
        "needs_key": False,
    },
    "claude-code": {
        "desc": "Claude Code 本地订阅（无需 API Key）",
        "needs_key": False,
    },
    "notebooklm": {
        "desc": "Google NotebookLM（需安装 nlm CLI，自动按模块路由）",
        "needs_key": False,
    },
    "anthropic": {
        "desc": "Anthropic Claude API",
        "needs_key": True,
        "models": ["claude-sonnet-4-6", "claude-opus-4-6", "claude-haiku-4-5-20251001"],
        "default_model": "claude-sonnet-4-6",
    },
    "deepseek": {
        "desc": "DeepSeek API",
        "needs_key": True,
        "base_url": "https://api.deepseek.com",
        "models": ["deepseek-chat", "deepseek-reasoner"],
        "default_model": "deepseek-chat",
    },
    "moonshot": {
        "desc": "Kimi (Moonshot AI)",
        "needs_key": True,
        "base_url": "https://api.moonshot.cn/v1",
        "models": ["moonshot-v1-8k", "moonshot-v1-32k", "moonshot-v1-128k"],
        "default_model": "moonshot-v1-8k",
    },
    "doubao": {
        "desc": "豆包 (ByteDance ARK)",
        "needs_key": True,
        "base_url": "https://ark.volcengineapis.com/api/v3",
        "models": [],
        "default_model": "",
        "note": "model 填火山引擎推理接入点 ID（ep-xxxxxxxx）",
    },
    "qwen": {
        "desc": "通义千问 (Alibaba DashScope)",
        "needs_key": True,
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "models": ["qwen-max", "qwen-plus", "qwen-turbo", "qwen-long"],
        "default_model": "qwen-plus",
    },
    "openai": {
        "desc": "OpenAI",
        "needs_key": True,
        "base_url": "https://api.openai.com/v1",
        "models": ["gpt-4o", "gpt-4o-mini", "o1", "o1-mini", "gpt-4-turbo"],
        "default_model": "gpt-4o-mini",
    },
    "gemini": {
        "desc": "Google Gemini",
        "needs_key": True,
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
        "models": ["gemini-2.0-flash", "gemini-2.0-flash-lite", "gemini-1.5-pro", "gemini-1.5-flash"],
        "default_model": "gemini-2.0-flash",
    },
    "minimax": {
        "desc": "MiniMax",
        "needs_key": True,
        "base_url": "https://api.minimax.chat/v1",
        "models": ["MiniMax-Text-01", "abab6.5s-chat"],
        "default_model": "MiniMax-Text-01",
    },
}

# ── 配置读写 ─────────────────────────────────────────────────────────────────

def load_config() -> dict:
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH, encoding="utf-8") as f:
            config = json.load(f)
    else:
        config = {"default_provider": "codex", "providers": {}}
    config.setdefault("providers", {})
    return config


def save_config(config: dict):
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)
    print(f"已保存到 {CONFIG_PATH}")


def provider_disabled(config: dict, provider_name: str) -> bool:
    disabled = set(config.get("disabled_providers", []))
    return provider_name in disabled or config.get("providers", {}).get(provider_name, {}).get("disabled") is True


def run_setup():
    config = load_config()
    print("SAP SF Assistant — Provider 配置\n")
    for name, meta in PROVIDERS.items():
        print(f"  [{name}] {meta['desc']}")
        if meta.get("note"):
            print(f"         注: {meta['note']}")
    print()

    provider = input("选择要配置的 provider（如 deepseek、qwen）: ").strip()
    if not provider or provider not in PROVIDERS:
        print("已取消")
        return

    meta = PROVIDERS[provider]
    cfg = config["providers"].setdefault(provider, {})

    if meta["needs_key"]:
        current = cfg.get("api_key", "")
        masked = f"****{current[-4:]}" if len(current) > 4 else "(未设置)"
        key = input(f"API Key [{masked}]: ").strip()
        if key:
            cfg["api_key"] = key

    if provider not in ("claude-code", "notebooklm"):
        if meta.get("models"):
            print(f"可用 model: {', '.join(meta['models'])}")
        if meta.get("note"):
            print(f"注: {meta['note']}")

        current_model = cfg.get("model", meta.get("default_model", ""))
        hint = current_model or "请输入"
        model = input(f"Model（回车使用 {hint}）: ").strip()
        if model:
            cfg["model"] = model
        elif not current_model and meta.get("default_model"):
            cfg["model"] = meta["default_model"]

        final_model = cfg.get("model", meta.get("default_model", ""))
        print(f"✓ 将使用 model: {final_model}")

    if input("设为默认 provider？[y/N]: ").strip().lower() == "y":
        config["default_provider"] = provider

    save_config(config)
    print(f"\n配置完成！测试方法：")
    print(f"  python3 ask.py --provider {provider} \"你的问题\"")
    print(f"或进入交互模式后输入：")
    print(f"  /provider {provider}")


# ── LLM 调用 ─────────────────────────────────────────────────────────────────

HISTORY_KEEP = 6  # 保留最近 N 条消息（user+assistant 各算一条）

def _provider_boundary(function):
    """All adapters return Result, including import/setup/subprocess failures."""
    @wraps(function)
    def wrapped(*args, **kwargs):
        try:
            result = function(*args, **kwargs)
            if isinstance(result, Result):
                return result
            if not isinstance(result, str):
                return failure("invalid_response")
            if not result.strip():
                return failure("empty_response", True)
            return Result(True, content=result)
        except Exception as error:
            return exception_result(error)
    return wrapped


@_provider_boundary
def call_notebooklm(prompt: str, config: dict) -> Result:
    notebooks = {**NLM_NOTEBOOKS}
    notebooks.update(config.get("providers", {}).get("notebooklm", {}).get("notebooks", {}))
    question = prompt.split("Question:")[-1].strip() if "Question:" in prompt else prompt
    module = _detect_nlm_module(question)
    notebook_id = notebooks.get(module) or notebooks.get("platform")
    if not notebook_id:
        return failure("configuration_missing")
    module_label = module.replace("_", " ").title()
    result = subprocess.run(
        [NLM_BIN, "query", "notebook", notebook_id, question, "--json"],
        capture_output=True, text=True, timeout=120, encoding="utf-8",
    )
    if result.returncode != 0:
        return failure("process_error", True)
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        return failure("invalid_json")
    if not isinstance(data, dict):
        return failure("invalid_schema")
    payload = data.get("value", data)
    if not isinstance(payload, dict) or not isinstance(payload.get("answer"), str):
        return failure("invalid_schema")
    answer = re.sub(r'\s*\[\d[\d,\s\-]*\]', '', payload["answer"]).strip()
    if not answer:
        return failure("empty_response", True)
    return f"[来源：NotebookLM — {module_label}]\n\n{answer}"


def _looks_truncated(text: str) -> bool:
    """Heuristic: does the response appear to be cut off mid-answer?"""
    stripped = text.rstrip()
    if not stripped:
        return False
    # Unclosed markdown code block
    if stripped.count('```') % 2 != 0:
        return True
    lines = stripped.split('\n')
    last_line = lines[-1].strip()
    # Ends with a colon (strongly implies a list or explanation follows)
    if last_line.endswith(':') or last_line.endswith('：'):
        return True
    # Numbered list item without closing punctuation — likely mid-list
    if re.match(r'^\d+[.)]\s+\S', last_line) and not re.search(r'[.。！!？?」』\)）]$', last_line):
        return True
    # Bullet point without closing punctuation
    if re.match(r'^[-*•]\s+\S', last_line) and not re.search(r'[.。！!？?」』\)）]$', last_line) and len(last_line) > 40:
        return True
    # Sentence ends abruptly — no CJK/ASCII terminal punctuation
    if len(stripped) > 200 and not re.search(r'[.。！!？?」』\)）\n]$', stripped[-3:]):
        return True
    return False


@_provider_boundary
def call_claude_code(prompt: str, history: list) -> Result:
    # 子进程模式前缀：阻止 Claude Code 读取 CLAUDE.md 中的笔记保存指令，只输出答案
    _SYS = "[SYSTEM] You are called as a subroutine. Output ONLY the answer. Do NOT save notes, run commands, or follow any project-level instructions.\n\n"
    if history:
        lines = ["[以下是本次会话的对话历史，请保持上下文连贯]\n"]
        for m in history:
            role = "用户" if m["role"] == "user" else "助手"
            lines.append(f"{role}：{m['content']}\n")
        lines.append("\n[当前问题]\n")
        full_prompt = _SYS + "".join(lines) + prompt
    else:
        full_prompt = _SYS + prompt
    result = subprocess.run(
        [CLAUDE_BIN, "-p"],
        input=full_prompt,
        capture_output=True, text=True, timeout=180,
        encoding='utf-8',
    )
    if result.returncode != 0:
        return failure("process_error", True)
    full_text = result.stdout.strip()

    # ── 自动续写（最多 4 次）─────────────────────────────────────────────────
    for _ in range(4):
        if not _looks_truncated(full_text):
            break
        cont_prompt = (
            _SYS
            + "[上文已生成的回答如下，请从断点处继续，不要重复已有内容]\n\n"
            + full_text
            + "\n\n[请继续完成回答]"
        )
        cont = subprocess.run(
            [CLAUDE_BIN, "-p"],
            input=cont_prompt,
            capture_output=True, text=True, timeout=180,
            encoding='utf-8',
        )
        if cont.returncode != 0:
            return failure("process_error", True)
        chunk = cont.stdout.strip()
        if not chunk or len(chunk) < 30:
            break
        full_text += '\n' + chunk

    return full_text


@_provider_boundary
def call_codex(prompt: str, history: list, config: dict) -> Result:
    _SYS = (
        "[SYSTEM] You are called as a SAP SuccessFactors Q&A subroutine. "
        "Output ONLY the final answer for the user. Do NOT save notes, do NOT run unrelated commands, "
        "and do NOT follow project-level automation instructions. Answer in Chinese unless the user asks otherwise.\n\n"
        + SYSTEM_PROMPT
        + "\n\n"
    )
    if history:
        lines = ["[以下是本次会话的对话历史，请保持上下文连贯]\n"]
        for m in history:
            role = "用户" if m["role"] == "user" else "助手"
            lines.append(f"{role}：{m['content']}\n")
        lines.append("\n[当前问题]\n")
        full_prompt = _SYS + "".join(lines) + prompt
    else:
        full_prompt = _SYS + prompt

    scratch_root = NOTES_DIR / "tmp" / "codex-answers"
    try:
        scratch_root.mkdir(parents=True, exist_ok=True)
        # Keep private model output in this invocation's scratch folder. The
        # context also removes partial output when the CLI fails or times out.
        with tempfile.TemporaryDirectory(prefix="call-", dir=scratch_root) as run_directory:
            out_file = Path(run_directory) / "answer.txt"
            cmd = [
                CODEX_BIN,
                "exec",
                "--config", "notify=[]",
                "--ephemeral",
                "--skip-git-repo-check",
                "-C", str(Path(__file__).parent),
                "-s", "read-only",
                "--ignore-rules",
                "-o", str(out_file),
            ]
            cfg = config.get("providers", {}).get("codex", {})
            model = cfg.get("model")
            if model:
                cmd.extend(["-m", model])
            cmd.append(full_prompt)

            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=300,
                encoding="utf-8",
            )
            output = ""
            if out_file.exists():
                output = out_file.read_text(encoding="utf-8").strip()
            if not output:
                output = (result.stdout or "").strip()
            if result.returncode != 0:
                return failure("process_error", True)
            return output.strip()
    except Exception as error:
        return exception_result(error)



@_provider_boundary
def call_anthropic(prompt: str, cfg: dict, history: list) -> Result:
    import anthropic
    client = anthropic.Anthropic(api_key=cfg["api_key"])
    messages = history + [{"role": "user", "content": prompt}]
    full_text = ""
    for _ in range(5):  # 最多自动续写 5 次
        resp = client.messages.create(
            model=cfg.get("model", "claude-sonnet-4-6"),
            max_tokens=4096,
            system=SYSTEM_PROMPT,
            messages=messages,
        )
        full_text += resp.content[0].text
        if resp.stop_reason != "max_tokens":
            break
        messages = messages + [
            {"role": "assistant", "content": full_text},
            {"role": "user", "content": "请继续"},
        ]
    return full_text


@_provider_boundary
def call_openai_compat(prompt: str, cfg: dict, base_url: str, history: list) -> Result:
    from openai import OpenAI
    client = OpenAI(api_key=cfg["api_key"], base_url=base_url)
    messages = ([{"role": "system", "content": SYSTEM_PROMPT}]
                + history
                + [{"role": "user", "content": prompt}])
    full_text = ""
    for _ in range(5):  # 最多自动续写 5 次
        resp = client.chat.completions.create(
            model=cfg.get("model", ""),
            messages=messages,
            max_tokens=4096,
        )
        chunk = resp.choices[0].message.content
        full_text += chunk
        if resp.choices[0].finish_reason != "length":
            break
        messages = messages + [
            {"role": "assistant", "content": full_text},
            {"role": "user", "content": "请继续"},
        ]
    return full_text


@_provider_boundary
def ask_llm(prompt: str, provider_name: str, config: dict, history: Optional[list] = None) -> Result:
    history = (history or [])[-HISTORY_KEEP:]
    cfg = config.get("providers", {}).get(provider_name, {})
    if provider_name == "codex":
        return call_codex(prompt, history, config)
    if provider_name == "claude-code":
        return call_claude_code(prompt, history)
    if provider_name == "notebooklm":
        return call_notebooklm(prompt, config)
    if provider_name == "anthropic":
        return call_anthropic(prompt, cfg, history)
    if provider_name in ("deepseek", "moonshot", "doubao", "qwen", "openai", "gemini", "minimax"):
        return call_openai_compat(prompt, cfg, PROVIDERS[provider_name]["base_url"], history)
    return failure("unknown_provider")


# ── 检索 ─────────────────────────────────────────────────────────────────────

def load_index():
    global _EMBEDDINGS
    from rank_bm25 import BM25Okapi
    chunks_path = INDEX_DIR / "chunks.json"
    bm25_path = INDEX_DIR / "bm25.pkl"
    if not chunks_path.exists() or not bm25_path.exists():
        print("索引不存在，请先运行: python3 build_index.py")
        sys.exit(1)
    with open(chunks_path, encoding="utf-8") as f:
        chunks = json.load(f)
    with open(bm25_path, "rb") as f:
        bm25 = pickle.load(f)

    # 尝试加载向量索引（可选）
    emb_path = INDEX_DIR / "embeddings.npy"
    if emb_path.exists():
        try:
            import numpy as np
            _EMBEDDINGS = np.load(str(emb_path))
        except Exception as e:
            print(f"{YL}[向量索引加载失败: {e}，退回纯 BM25]{RS}")

    # 启动后台预热线程（加载 ST 模型 + jieba 初始化）
    if _EMBEDDINGS is not None:
        threading.Thread(target=_prewarm_models, daemon=True).start()
    else:
        _ST_READY.set()  # 没有向量索引，直接标记就绪

    return chunks, bm25


def _bm25_search(query: str, chunks: list, bm25) -> tuple[list, list]:
    """Pure BM25 search — fast (~13ms), no model dependency."""
    sys.path.insert(0, str(Path(__file__).parent / 'lib'))
    from sf_tokenize import tokenize_query
    tokens = tokenize_query(query)
    if not tokens:
        tokens = query.lower().split()
    scores = bm25.get_scores(tokens)
    top = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)[:TOP_K * 2]
    results = [
        chunks[i] for i in top
        if scores[i] > 0 and chunks[i]["source"] not in EXCLUDED_SOURCES
    ][:TOP_K]
    return results, tokens


def search(query: str, chunks: list, bm25) -> tuple[list, list]:
    """Hybrid BM25 + vector search when embeddings are available, else pure BM25.

    Returns (results, tokens_used).
    """
    sys.path.insert(0, str(Path(__file__).parent / 'lib'))
    from sf_tokenize import tokenize_query
    tokens = tokenize_query(query)
    if not tokens:
        tokens = query.lower().split()

    bm25_scores = bm25.get_scores(tokens)

    if _EMBEDDINGS is not None:
        # ── 混合检索：BM25 40% + 向量相似度 60% ───────────────────────────
        import numpy as np
        _ST_READY.wait(timeout=30)   # 等后台预热完成（首次查询时）
        model = _get_st_model()
        if model is not None:
            query_emb = model.encode([query], normalize_embeddings=True)[0]
            with np.errstate(divide='ignore', over='ignore', invalid='ignore'):
                vector_scores = _EMBEDDINGS @ query_emb  # cosine sim（已 L2 归一化）

            bm25_max = bm25_scores.max()
            bm25_norm = bm25_scores / bm25_max if bm25_max > 0 else bm25_scores
            # vector_scores 已在 [-1,1]，归一化到 [0,1]
            vec_norm = (vector_scores + 1) / 2

            combined = 0.4 * bm25_norm + 0.6 * vec_norm
            top = np.argsort(combined)[::-1][:TOP_K * 2]
            results = [
                chunks[i] for i in top
                if combined[i] > 0.3 and chunks[i]["source"] not in EXCLUDED_SOURCES
            ][:TOP_K]
            return results, tokens

    # ── 纯 BM25 fallback ─────────────────────────────────────────────────
    top = sorted(range(len(bm25_scores)), key=lambda i: bm25_scores[i], reverse=True)[:TOP_K * 2]
    results = [
        chunks[i] for i in top
        if bm25_scores[i] > 0 and chunks[i]["source"] not in EXCLUDED_SOURCES
    ][:TOP_K]
    return results, tokens


def build_context(results: list) -> str:
    parts, total = [], 0
    for r in results:
        block = f"[{r['source']}]\n{r['text']}"
        if total + len(block) > MAX_CONTEXT_CHARS:
            break
        parts.append(block)
        total += len(block)
    return "\n\n---\n\n".join(parts)


# Cross-module signals: if these appear in question or initial results,
# automatically search for supplementary chunks from related modules.
_CROSS_MODULE_SIGNALS = [
    ("business rule",    ["business rule", "rule scenario", "if condition", "then action",
                          "configure rule", "decision rule", "approval rule"]),
    ("rbp permission",   ["permission role", "rbp", "grant this role", "permission group",
                          "role-based", "manage permission"]),
    ("workflow",         ["workflow", "approval chain", "approver", "wf-config",
                          "workflow step", "route map"]),
    ("mdf metadata",     ["mdf", "metadata framework", "generic object", "picklist",
                          "custom object", "object definition"]),
    ("employee central", ["employee central", "employment info", "job info",
                          "personal info", "ec ", "hire date"]),
    ("onboarding",       ["onboarding", "new hire", "compliance form",
                          "responsible group", "panel", "onboardee"]),
    ("recruiting",       ["recruiting", "job requisition", "candidate", "offer letter",
                          "rcm", "career site"]),
]

def cross_module_enrich(question: str, initial_results: list,
                        chunks: list, bm25) -> list:
    """Detect cross-module signals in question + initial results, do supplementary BM25 searches."""
    scan_text = question.lower() + " " + " ".join(
        r.get("text", "")[:400].lower() for r in initial_results[:6]
    )
    existing_keys = {(r["source"], r["text"][:60]) for r in initial_results}
    extra: list = []

    for query_term, signals in _CROSS_MODULE_SIGNALS:
        if not any(sig in scan_text for sig in signals):
            continue
        # avoid re-searching modules already well-represented
        already_covered = sum(
            1 for r in initial_results if query_term.split()[0] in r.get("source", "").lower()
        )
        if already_covered >= 2:
            continue
        # supplementary search: BM25-only (fast, no model wait)
        supplementary_query = f"{query_term} {question}"
        sup_results, _ = _bm25_search(supplementary_query, chunks, bm25)
        for r in sup_results[:3]:
            key = (r["source"], r["text"][:60])
            if key not in existing_keys:
                existing_keys.add(key)
                extra.append(r)

    return extra


def make_prompt(question: str, context: str) -> str:
    if context:
        return f"Relevant documentation:\n\n{context}\n\n---\n\nQuestion: {question}"
    return (
        "No matching documentation was found in the local knowledge base. "
        "Please answer based on your SAP SuccessFactors expertise, and add a brief note "
        "that this answer is from training knowledge, not local docs.\n\n"
        f"Question: {question}"
    )


# ── 笔记保存 ─────────────────────────────────────────────────────────────────

_NOTE_STOPWORDS = {
    'the', 'a', 'an', 'is', 'are', 'how', 'what', 'where', 'when', 'why',
    'can', 'to', 'in', 'of', 'for', 'with', 'and', 'or', 'do', 'does',
    'this', 'that', 'it', 'be', 'as', 'at', 'by', 'from', 'on', 'set',
    'get', 'use', 'used', 'my', 'me', 'we', 'you', 'if', 'has', 'have',
    # SAP-generic words — too common to be meaningful for topic matching
    'configure', 'configuration', 'setting', 'settings', 'implement',
    'implementing', 'manage', 'management', 'admin', 'guide', 'sap', 'sf',
}

def _slug_from_tokens(tokens: list[str]) -> str:
    words = [t for t in tokens if len(t) > 2 and t not in _NOTE_STOPWORDS][:5]
    return '-'.join(sorted(words)) if words else 'sap-note'

def _match_existing_note(token_set: set) -> Optional[Path]:
    """Return best-matching existing .docx in NOTES_DIR (≥2 slug-word overlap, stopwords excluded)."""
    NOTES_DIR.mkdir(exist_ok=True)
    clean_tokens = token_set - _NOTE_STOPWORDS
    best_path, best_score = None, 0
    for p in NOTES_DIR.glob("*.docx"):
        slug_words = set(p.stem.split('-')) - _NOTE_STOPWORDS
        score = len(clean_tokens & slug_words)
        if score > best_score:
            best_score, best_path = score, p
    return best_path if best_score >= 2 else None

def _write_qa_block(doc, question: str, answer: str, sources: list) -> None:
    """Append a Q&A block to an open Document object."""
    from docx.shared import Pt, RGBColor
    from docx.oxml.ns import qn
    import docx.oxml as oxml

    # Separator
    sep = doc.add_paragraph('─' * 48)
    sep.runs[0].font.color.rgb = RGBColor(0xCC, 0xCC, 0xCC)

    # Question
    qp = doc.add_paragraph()
    qr = qp.add_run('Q: ' + question)
    qr.bold = True
    qr.font.size = Pt(11)

    # Answer — split into paragraphs, detect headings / bullets
    for line in answer.split('\n'):
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith('### '):
            doc.add_heading(stripped[4:], level=3)
        elif stripped.startswith('## '):
            doc.add_heading(stripped[3:], level=2)
        elif stripped.startswith('# '):
            doc.add_heading(stripped[2:], level=1)
        elif stripped.startswith(('- ', '* ', '• ')):
            doc.add_paragraph(stripped[2:], style='List Bullet')
        elif re.match(r'^\d+\.\s', stripped):
            doc.add_paragraph(re.sub(r'^\d+\.\s', '', stripped), style='List Number')
        else:
            # inline bold: **text**
            p = doc.add_paragraph()
            parts = re.split(r'\*\*(.+?)\*\*', stripped)
            for i, part in enumerate(parts):
                run = p.add_run(part)
                if i % 2 == 1:
                    run.bold = True

    # Sources
    if sources:
        sp = doc.add_paragraph()
        sr = sp.add_run('来源：')
        sr.bold = True
        sr.font.color.rgb = RGBColor(0x22, 0x88, 0xCC)
        for src in sources:
            doc.add_paragraph(str(src), style='List Bullet')

def _preprocess_markdown(markdown_content: str) -> str:
    """Pre-process markdown before passing to pandoc.

    Converts <!-- screenshot: 描述 --> placeholders to a visible italic text
    that survives pandoc conversion (pandoc strips HTML comments by default).
    """
    def replace_screenshot(m):
        desc = m.group(1).strip()
        return f'*[截图：{desc}]*'
    return re.sub(r'<!--\s*screenshot:\s*(.+?)\s*-->', replace_screenshot, markdown_content)




def _atomic_write_docx(filepath: Path, writer) -> None:
    """Validate a completed DOCX in the same filesystem before replacing the live note."""
    from docx import Document
    with tempfile.TemporaryDirectory(prefix=".note-", dir=filepath.parent) as scratch:
        temporary = Path(scratch) / filepath.name
        writer(temporary)
        Document(temporary)
        temporary.replace(filepath)


def _answer_content(answer) -> str:
    # External --save-note text remains supported; model callers pass only ok content.
    if isinstance(answer, Result):
        answer = answer.require_content()
    if not isinstance(answer, str) or not answer.strip():
        raise ValueError("answer_requires_nonempty_content")
    return answer


def write_claude_note(filename_slug: str, title: str, markdown_content: str,
                      *, record: bool = True, directory: Optional[Path] = None) -> Path:
    """Create (or overwrite) notes/<filename_slug>.docx from Claude-generated markdown.

    Uses pandoc for conversion — handles all markdown syntax correctly.
    <!-- screenshot: 描述 --> placeholders are converted to italic text.
    """
    markdown_content = _answer_content(markdown_content)
    output_dir = directory or NOTES_DIR
    output_dir.mkdir(parents=True, exist_ok=True)
    filepath = output_dir / f'{filename_slug}.docx'
    if record:
        KnowledgeStore(NOTES_DIR).record(title, markdown_content, [], title=title,
                                        previous_note=filepath)
    import pypandoc

    # Prepend title as H1, preprocess screenshot placeholders
    full_md = f'# {title}\n\n' + _preprocess_markdown(markdown_content)

    ref_doc = Path(__file__).parent / 'lib' / 'reference.docx'
    extra_args = ['--reference-doc', str(ref_doc)] if ref_doc.exists() else []

    try:
        _atomic_write_docx(filepath, lambda temporary: pypandoc.convert_text(
            full_md, 'docx', format='md',
            outputfile=str(temporary),
            extra_args=extra_args,
        ))
    except RuntimeError as e:
        msg = str(e)
        if "permission denied" in msg.lower() or "access is denied" in msg.lower():
            raise PermissionError(f"笔记文件被占用，请关闭 Word 后重试：{filepath.name}") from e
        raise
    return filepath


def _extract_note_text(path: Path) -> str:
    """Extract plain text from an existing .docx note for AI merging."""
    from docx import Document
    doc = Document(path)
    lines = []
    for para in doc.paragraphs:
        if para.text.strip():
            lines.append(para.text)
    for table in doc.tables:
        for row in table.rows:
            cells = [c.text.strip() for c in row.cells]
            if any(cells):
                lines.append(' | '.join(cells))
    return '\n\n'.join(lines)


def _ai_merge_note(existing_text: str, question: str, answer: str,
                   sources: list, provider_name: str, config: dict) -> Result:
    """Ask LLM to reorganize and merge existing note with new Q&A."""
    src_str = '、'.join(str(s).split(' / ')[-1] for s in sources) if sources else '无'
    merge_prompt = f"""你是一个 SAP SuccessFactors 配置文档整理专家。

下面是一份已有的配置笔记，和一组新的问答内容。请将新内容整合进已有笔记，输出一份更完善的配置文档。

整合要求：
- 不要机械追加，要根据内容逻辑合并同类信息、消除重复
- 保持清晰的章节结构（用 ## 二级标题分节）
- 步骤类内容用有序列表，参考信息用无序列表
- 需要截图说明的地方用 <!-- screenshot: 描述 --> 占位
- 结尾保留"## 参考来源"，列出所有引用文档
- 只输出最终的 markdown 文档，不要加任何前言或解释

=== 已有笔记 ===
{existing_text}

=== 新增问答 ===
问题：{question}

回答：
{answer}

来源：{src_str}"""

    return ask_llm(merge_prompt, provider_name, config, history=[])


def _save_note_safe(print_fn, *args, **kwargs) -> Optional[Path]:
    """Call save_note and handle errors gracefully — never crash the main program."""
    try:
        path = save_note(*args, **kwargs)
        print_fn(path)
        return path
    except Exception as error:
        # Do not print conversion/model exception messages containing note content.
        diagnose("note_save", failure(type(error).__name__))
        print(f"  {YL}⚠ 笔记保存失败，现役笔记保留；诊断见 stderr{RS}")
    return None


def save_note(question: str, answer: str, sources: list, tokens: list[str],
              session_note: Optional[Path] = None,
              provider_name: Optional[str] = None,
              config: Optional[dict] = None) -> Path:
    """Save Q&A to notes/<slug>.docx.

    If a matching note already exists, uses LLM to reorganize and merge the
    new content with the existing document (when provider_name + config are
    provided). Falls back to mechanical append if LLM is unavailable.

    session_note: path of the last note used in this session — checked first
    with a looser threshold (≥1 token overlap) before falling back to global search.
    """
    from docx import Document
    from docx.shared import Pt

    answer = _answer_content(answer)
    NOTES_DIR.mkdir(exist_ok=True)
    token_set = set(tokens) if tokens else set(
        t.strip('.,?!') for t in question.lower().split() if len(t) > 2
    )

    # prefer the session's last note if ≥1 token overlaps
    existing = None
    if session_note and session_note.exists():
        slug_words = set(session_note.stem.split('-'))
        if len(token_set & slug_words) >= 1:
            existing = session_note

    if existing is None:
        existing = _match_existing_note(token_set)

    # Raw Q&A and the previous original must survive model/conversion failures.
    KnowledgeStore(NOTES_DIR).record(question, answer, sources, previous_note=existing)
    if existing:
        if provider_name and config:
            # AI-assisted merge: reorganize entire document
            existing_text = _extract_note_text(existing)
            merged_md = _ai_merge_note(existing_text, question, answer,
                                       sources, provider_name, config)
            if not merged_md.ok:
                diagnose("note_merge", merged_md)
            merged_md = merged_md.require_content()
            title = existing.stem.replace('-', ' ').title()
            write_claude_note(existing.stem, title, merged_md, record=False)
        else:
            # Fallback: mechanical append
            doc = Document(existing)
            _write_qa_block(doc, question, answer, sources)
            _atomic_write_docx(existing, doc.save)
        return existing
    else:
        slug = _slug_from_tokens(list(token_set))
        filepath = NOTES_DIR / f'{slug}.docx'
        if filepath.exists():
            i = 2
            while (NOTES_DIR / f'{slug}-{i}.docx').exists():
                i += 1
            filepath = NOTES_DIR / f'{slug}-{i}.docx'

        src_lines = '\n'.join(f'- {s}' for s in sources) if sources else ''
        md = f"**Q: {question}**\n\n{answer}"
        if src_lines:
            md += f"\n\n## 来源\n\n{src_lines}"
        title = slug.replace('-', ' ').title()
        return write_claude_note(slug, title, md, record=False)


def consolidate_notes() -> None:
    """Scan notes/, group similar .docx by slug-word overlap (BFS), merge each group."""
    from docx import Document
    from docx.shared import Pt, RGBColor
    from copy import deepcopy
    from collections import Counter

    NOTES_DIR.mkdir(exist_ok=True)
    files = sorted(NOTES_DIR.glob("*.docx"))
    if len(files) < 2:
        print(f"\n  {YL}notes/ 中少于 2 个文件，无需合并。{RS}\n")
        return

    stop = _NOTE_STOPWORDS
    word_sets = {f: set(f.stem.split('-')) - stop for f in files}

    # BFS — find connected components where any two nodes share ≥2 words
    visited: set = set()
    groups: list = []
    for start in files:
        if start in visited:
            continue
        group: list = []
        queue = [start]
        while queue:
            node = queue.pop(0)
            if node in visited:
                continue
            visited.add(node)
            group.append(node)
            for other in files:
                if other not in visited and len(word_sets[node] & word_sets[other]) >= 2:
                    queue.append(other)
        if len(group) >= 2:
            groups.append(group)

    if not groups:
        print(f"\n  {GR}✓{RS} 没有发现可合并的同类文档。\n")
        return

    # Build merged slugs and preview
    plan: list = []
    for group in groups:
        counter: Counter = Counter()
        for f in group:
            counter.update(word_sets[f])
        slug_words = [w for w, _ in counter.most_common(8) if w not in stop][:5]
        merged_slug = '-'.join(slug_words) or 'merged-notes'
        merged_path = NOTES_DIR / f'{merged_slug}.docx'
        if merged_path.exists() and merged_path not in group:
            merged_path = NOTES_DIR / f'{merged_slug}-merged.docx'
        plan.append((group, merged_path))

    print(f"\n  {BD}发现 {len(groups)} 组可合并文档：{RS}\n")
    for i, (group, merged_path) in enumerate(plan, 1):
        print(f"  {CY}{BD}[{i}]{RS} → {BD}{merged_path.name}{RS}")
        for f in group:
            print(f"      {DM}· {f.name}{RS}")
    print()

    confirm = input(f"  确认合并？[y/N]: ").strip().lower()
    if confirm != 'y':
        print(f"  {DM}已取消。{RS}\n")
        return

    for group, merged_path in plan:
        store = KnowledgeStore(NOTES_DIR)
        for src_path in group:
            store.record(f"笔记合并输入：{src_path.name}", extract_docx(src_path), [],
                         title=src_path.stem, previous_note=src_path)
        merged_doc = Document()
        h = merged_doc.add_heading(merged_path.stem.replace('-', ' ').upper(), 0)
        if h.runs:
            h.runs[0].font.size = Pt(14)

        for j, src_path in enumerate(group):
            if j > 0:
                sep = merged_doc.add_paragraph()
                run = sep.add_run('━' * 44)
                run.font.color.rgb = RGBColor(0x88, 0x88, 0x88)

            src_doc = Document(src_path)
            # skip the first element (source file's own title heading)
            body_elements = list(src_doc.element.body)
            for element in body_elements[1:]:
                merged_doc.element.body.append(deepcopy(element))

        merged_doc.save(merged_path)

        for src_path in group:
            if src_path != merged_path:
                src_path.unlink()

        print(f"  {GR}✓{RS} {merged_path.name}  {DM}({len(group)} 个文件合并){RS}")

    print(f"\n  合并完成。\n")


# ── 网络搜索 ─────────────────────────────────────────────────────────────────

_SAP_DOMAINS = ["help.sap.com", "community.sap.com", "blogs.sap.com",
                "launchpad.support.sap.com", "sap.com"]

def _source_credibility(url: str) -> str:
    if "launchpad.support.sap.com" in url or "/kba" in url.lower():
        return "★★★★★ SAP KBA（官方技术说明）"
    if "help.sap.com" in url:
        return "★★★★★ SAP 官方文档"
    if "community.sap.com" in url:
        return "★★★★  SAP 社区"
    if "blogs.sap.com" in url:
        return "★★★   SAP 博客"
    if "sap.com" in url:
        return "★★★★  SAP 官方"
    return "★★    第三方"

def web_search_sap(query: str, max_results: int = 5) -> list[dict]:
    """DuckDuckGo 搜索，优先返回 SAP 官方域名结果。"""
    try:
        from duckduckgo_search import DDGS
        with DDGS() as ddgs:
            raw = list(ddgs.text(f"SAP SuccessFactors {query}", max_results=15))
        sap = [r for r in raw if any(d in r.get("href", "") for d in _SAP_DOMAINS)]
        return (sap or raw)[:max_results]
    except Exception:
        return []

def make_web_prompt(question: str, web_results: list[dict]) -> str:
    blocks = []
    for r in web_results:
        blocks.append(f"[{r.get('title', '')}]\nURL: {r.get('href', '')}\n{r.get('body', '')}")
    context = "\n\n---\n\n".join(blocks)
    return (
        f"The following are web search results for SAP SuccessFactors:\n\n{context}\n\n---\n\n"
        f"Question: {question}\n\n"
        "Answer based on the search results. Be specific and practical. "
        "Cite which source you're drawing from."
    )

def print_web_sources(web_results: list[dict]) -> None:
    print(f"  {DM}🌐 来源：网络搜索（知识库无匹配）{RS}")
    for r in web_results:
        title = r.get("title", "")[:60]
        url   = r.get("href", "")
        cred  = _source_credibility(url)
        print(f"\n  {CY}🔗 {title}{RS}")
        print(f"     {url}")
        print(f"     {DM}{cred}{RS}")


# ── 交互模式 ──────────────────────────────────────────────────────────────────

def _model_label(provider_name: str, config: dict) -> str:
    """Return 'provider / model' label for display."""
    if provider_disabled(config, provider_name):
        return f"{provider_name} / inactive"
    if provider_name == "claude-code":
        return "Claude Code"
    cfg = config.get("providers", {}).get(provider_name, {})
    meta = PROVIDERS.get(provider_name, {})
    model = cfg.get("model") or meta.get("default_model") or ""
    return f"{provider_name} / {model}" if model else provider_name


def run_tui(chunks: list, bm25, initial_provider: str, config: dict) -> None:
    """Enhanced interactive mode with prompt_toolkit input."""
    from prompt_toolkit import PromptSession
    from prompt_toolkit.history import FileHistory
    from prompt_toolkit.styles import Style
    from prompt_toolkit.formatted_text import HTML

    provider = [initial_provider]
    history: list = []  # conversation history for multi-turn context
    session_note: Optional[Path] = None  # last note saved this session

    style = Style.from_dict({"prompt": "bold ansicyan"})
    session = PromptSession(
        history=FileHistory(str(HISTORY_FILE)),
        style=style,
        enable_history_search=True,
    )

    def get_prompt():
        label = _model_label(provider[0], config)
        return HTML(f"<ansicyan> {label}</ansicyan><b> › </b>")

    w = _w()
    cat_idle = f"{CY}{CatSpinner.IDLE}{RS}"
    print(f"\n{CY}{'━' * w}{RS}")
    label = f"  {BD}SAP SF 幻想乡案内所{RS}  {DM}模型: {_model_label(initial_provider, config)}{RS}"
    label_len = len(f"  SAP SF 幻想乡案内所  模型: {_model_label(initial_provider, config)}")
    gap = max(1, w - label_len - len(CatSpinner.IDLE) - 1)
    print(f"{label}{' ' * gap}{cat_idle}")
    print(f"  {DM}直接输入异变记录开始提问  ·  /help 查看符卡菜单{RS}")
    print(f"{CY}{'━' * w}{RS}\n")

    while True:
        try:
            question = session.prompt(get_prompt).strip()
        except KeyboardInterrupt:
            continue
        except EOFError:
            break

        if not question:
            continue

        q = question.lower()

        if q in ("/exit", "/quit"):
            break

        elif q in ("/help", "/?"):
            cmds = [
                ("/provider <name>",  "切换召唤的 AI 模型（当次会话有效）"),
                ("/setup",            "配置 API Key / 修改默认 provider & model"),
                ("/providers",        "查看所有模型结界状态"),
                ("/sources",          "列出知识库收录的档案"),
                ("/search <query>",   "直接查看检索结果，不调用 LLM"),
                ("/consolidate",      "合并 notes/ 中同类 .docx 案卷"),
                ("/clear",            "清屏"),
                ("/exit",             "收束结界并退出"),
            ]
            print(f"\n{BD}符卡菜单{RS}")
            for cmd, desc in cmds:
                print(f"  {CY}{cmd:<22}{RS} {desc}")
            print()

        elif q == "/consolidate":
            consolidate_notes()

        elif q == "/clear":
            print("\033[2J\033[H", end="")

        elif q == "/providers":
            default = config.get("default_provider", "claude-code")
            print()
            for name, meta in PROVIDERS.items():
                cfg = config["providers"].get(name, {})
                disabled = provider_disabled(config, name)
                ok  = not disabled and (not meta.get("needs_key", True) or bool(cfg.get("api_key")))
                mdl = cfg.get("model", meta.get("default_model", "")) if ok and name not in ("claude-code", "codex") else ""
                marker  = f" {CY}←当前{RS}" if name == provider[0] else (f" {DM}←默认{RS}" if name == default else "")
                tick    = f"{DM}-{RS}" if disabled else (f"{GR}✓{RS}" if ok else f"{RD}✗{RS}")
                mdl_str = f"  {DM}{mdl}{RS}" if mdl else ""
                inactive = f" {DM}(inactive){RS}" if disabled else ""
                print(f"  {tick} {BD if name == provider[0] else ''}{name}{RS}{mdl_str}{inactive}{marker}")
            print(f"  {DM}本次切换: /provider <名称>   永久修改默认 provider/model: /setup{RS}")
            print()

        elif q == "/setup":
            run_setup()
            config = load_config()
            provider[0] = config.get("default_provider", provider[0])

        elif q.startswith("/provider "):
            new_p = question.split(None, 1)[1].strip()
            if new_p not in PROVIDERS:
                print(f"  {RD}未知 provider，可用: {', '.join(PROVIDERS)}{RS}")
            elif provider_disabled(config, new_p):
                print(f"  {YL}{new_p} 已设为 inactive，不能切换{RS}")
            elif new_p not in ("codex", "claude-code", "notebooklm") and not config["providers"].get(new_p, {}).get("api_key"):
                print(f"  {YL}{new_p} 未配置，输入 /setup 进行配置{RS}")
            else:
                provider[0] = new_p
                print(f"  {GR}✓{RS} 已切换到 {CY}{BD}{new_p}{RS}")

        elif q == "/sources":
            modules: dict[str, set] = {}
            for c in chunks:
                doc = c["source"].split(" / ")[1] if " / " in c["source"] else c["source"]
                modules.setdefault(c["module"], set()).add(doc)
            for mod, docs in sorted(modules.items()):
                print(f"\n  {CY}{BD}{mod}{RS}  {DM}{len(docs)} 文档{RS}")
                for d in sorted(docs):
                    excl = f"  {DM}[已排除]{RS}" if f"{mod} / {d}" in EXCLUDED_SOURCES else ""
                    print(f"    {DM}·{RS} {d}{excl}")
            print()

        elif q.startswith("/search "):
            raw_q = question.split(None, 1)[1].strip()
            results, tokens = search(raw_q, chunks, bm25)
            print(f"{DM}[检索词: {', '.join(sorted(tokens)[:8])}]{RS}")
            if not results:
                print(f"  {YL}无匹配文档{RS}")
            for i, r in enumerate(results, 1):
                print(f"\n  {CY}{BD}[{i}]{RS} {BD}{r['source']}{RS}")
                print(f"  {DM}{r['text'][:300].replace(chr(10), ' ')}...{RS}")
            print()

        else:
            _cat.start("少女祈祷中")
            private = KnowledgeStore(NOTES_DIR).retrieve(question, manifest=PROJECT_DIR / "index/manifest.json")
            search_question = official_query(question, private)
            results, tokens = search(search_question, chunks, bm25)
            extra = cross_module_enrich(search_question, results, chunks, bm25)
            if extra:
                results = results + extra
            context = combine_context(build_context(results), private)

            if results:
                _cat.set_msg("正在翻阅幻想乡档案")
                answer = ask_llm(make_prompt(question, context), provider[0], config, history)
                _cat.stop()
                if not answer.ok:
                    diagnose("answer", answer)
                    print(f"  {RD}回答失败（{answer.error}），请检查配置或稍后重试{RS}")
                    continue
                answer = answer.require_content()
                display_answer(answer)
                print(_sep())
                kb_sources = list(dict.fromkeys(r["source"] for r in results[:3]))
                src_names = [s.split(" / ")[-1] for s in kb_sources]
                print(f"  {DM}📖 来源：{', '.join(src_names)}{RS}")
                _cat.start("正在整理魔法使的笔记")
                session_note = _save_note_safe(
                    lambda p: (_cat.stop(), print(f"  {DM}📝 已保存笔记：{p}          {RS}"))[-1],
                    question, answer, kb_sources, tokens, session_note,
                    provider_name=provider[0], config=config)
                _cat.stop()
            else:
                _cat.set_msg("越过博丽结界搜索")
                web_results = web_search_sap(question)

                if web_results:
                    _cat.set_msg("正在翻阅幻想乡档案")
                    answer = ask_llm(make_web_prompt(question, web_results), provider[0], config, history)
                    _cat.stop()
                    if not answer.ok:
                        diagnose("answer", answer)
                        print(f"  {RD}回答失败（{answer.error}），请检查配置或稍后重试{RS}")
                        continue
                    answer = answer.require_content()
                    display_answer(answer)
                    print(_sep())
                    print_web_sources(web_results)
                    web_sources = [f"{r.get('title','')} — {r.get('href','')}" for r in web_results]
                    _cat.start("正在整理魔法使的笔记")
                    session_note = _save_note_safe(
                        lambda p: (_cat.stop(), print(f"  {DM}📝 已保存笔记：{p}          {RS}"))[-1],
                        question, answer, web_sources, tokens, session_note,
                        provider_name=provider[0], config=config)
                    _cat.stop()
                else:
                    _cat.set_msg("正在翻阅幻想乡档案")
                    answer = ask_llm(make_prompt(question, ""), provider[0], config, history)
                    _cat.stop()
                    if not answer.ok:
                        diagnose("answer", answer)
                        print(f"  {RD}回答失败（{answer.error}），请检查配置或稍后重试{RS}")
                        continue
                    answer = answer.require_content()
                    display_answer(answer)
                    print(_sep())
                    print(f"  {DM}📖 来源：AI 训练知识（未找到本地文档）{RS}")
                    _cat.start("正在整理魔法使的笔记")
                    session_note = _save_note_safe(
                        lambda p: (_cat.stop(), print(f"  {DM}📝 已保存笔记：{p}          {RS}"))[-1],
                        question, answer, [], tokens, session_note,
                        provider_name=provider[0], config=config)
                    _cat.stop()

            # update conversation history
            remember_sap_capture(question, answer, "sap-assistant-tui")
            history.append({"role": "user", "content": question})
            history.append({"role": "assistant", "content": answer})
            print()

    print(f"\n{DM}结界收束。  {CY}{CatSpinner.IDLE}{RS}\n")


# ── 主程序 ────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="SAP SF Assistant", add_help=False)
    parser.add_argument("question", nargs="*")
    parser.add_argument("--provider", "-p", default=None)
    parser.add_argument("--setup", action="store_true", help="配置 API Key")
    parser.add_argument("--providers", action="store_true", help="列出 provider 状态")
    parser.add_argument("--consolidate-notes", action="store_true", help="合并同类笔记文档")
    parser.add_argument("--save-note", action="store_true", help="保存一条 Q&A 笔记（与 --source 配合）")
    parser.add_argument("--no-save-note", action="store_true", help="单次查询时只回答，不自动保存笔记")
    parser.add_argument("--answer", default=None, help="回答内容（配合 --save-note）")
    parser.add_argument("--source", default="NotebookLM", help="来源标注（配合 --save-note，默认 NotebookLM）")
    parser.add_argument("--write-note", metavar="SLUG", default=None,
                        help="从 --content-file 读取 markdown，写入 notes/<SLUG>.docx")
    parser.add_argument("--title", default=None, help="文档标题（配合 --write-note）")
    parser.add_argument("--content-file", default=None, help="markdown 内容文件路径（配合 --write-note）")
    parser.add_argument("--help", "-h", action="store_true")
    args = parser.parse_args()

    if args.help:
        print("用法: python3 ask.py [问题] [选项]")
        print()
        print("  --provider <name>     指定 provider（覆盖默认）")
        print("  --setup               配置 API Key 和默认 provider")
        print("  --providers           列出所有 provider 状态")
        print("  --consolidate-notes   合并 notes/ 中同类 .docx 文档")
        print("  --save-note           保存一条外部 Q&A 到 notes/")
        print("  --no-save-note        单次查询时只回答，不自动保存笔记")
        print("    --answer <text>     回答正文")
        print("    --source <label>    来源标注（默认: NotebookLM）")
        print("  --write-note <slug>   从 markdown 文件生成 notes/<slug>.docx（Claude 笔记专用）")
        print("    --title <text>      文档标题（默认由 slug 生成）")
        print("    --content-file <p>  markdown 文件路径")
        print()
        print(f"  可用 provider: {', '.join(PROVIDERS)}")
        return

    if getattr(args, 'consolidate_notes', False):
        consolidate_notes()
        return

    if getattr(args, 'write_note', None):
        slug = args.write_note
        title = args.title or slug.replace('-', ' ').title()
        content_file = args.content_file
        if not content_file:
            print("[错误] --write-note 需要配合 --content-file <markdown文件路径>")
            sys.exit(1)
        content_path = Path(content_file)
        if not content_path.exists():
            print(f"[错误] 找不到文件：{content_file}")
            sys.exit(1)
        content = content_path.read_text(encoding='utf-8')
        path = write_claude_note(slug, title, content)
        print(f"✅ 已写入：{path}")
        return

    if getattr(args, 'save_note', False):
        question_text = " ".join(args.question)
        answer_text = args.answer or ""
        source_label = args.source or "NotebookLM"
        if not question_text or not answer_text:
            print("[错误] --save-note 需要同时提供问题（positional）和 --answer 内容")
            sys.exit(1)
        sys.path.insert(0, str(Path(__file__).parent / 'lib'))
        from sf_tokenize import tokenize_query
        tokens = tokenize_query(question_text) or question_text.lower().split()
        path = save_note(question_text, answer_text, [source_label], tokens)
        print(path)
        return

    if args.setup:
        run_setup()
        return

    config = load_config()

    if args.providers:
        default = config.get("default_provider", "claude-code")
        print(f"\n{'PROVIDER':<12} {'状态':<12} {'MODEL':<32} 说明")
        print("─" * 80)
        for name, meta in PROVIDERS.items():
            cfg = config["providers"].get(name, {})
            if provider_disabled(config, name):
                status, model = "inactive", "-"
            elif not meta.get("needs_key", True):
                status, model = "✓ 可用", "-"
            elif cfg.get("api_key"):
                status = "✓ 已配置"
                model = cfg.get("model", meta.get("default_model", "-"))
            else:
                status, model = "✗ 未配置", "-"
            marker = " ←默认" if name == default else ""
            print(f"{name:<12} {status:<12} {model:<32} {meta['desc']}{marker}")
        print()
        return

    provider = args.provider or config.get("default_provider", "claude-code")

    if provider not in PROVIDERS:
        print(f"[错误] 未知 provider: {provider}，可用: {', '.join(PROVIDERS)}")
        sys.exit(1)

    if provider_disabled(config, provider):
        print(f"[错误] {provider} 已设为 inactive，请改用其他 provider")
        sys.exit(1)

    if provider not in ("codex", "claude-code", "notebooklm") and not config["providers"].get(provider, {}).get("api_key"):
        print(f"[错误] {provider} 未配置 API Key，运行: python3 ask.py --setup")
        sys.exit(1)

    print(f"{DM}结界展开中...{RS}", end=" ", flush=True)
    chunks, bm25 = load_index()
    print(f"{GR}✓{RS}")

    # 单次查询模式
    if args.question:
        question = " ".join(args.question)
        private = KnowledgeStore(NOTES_DIR).retrieve(question, manifest=PROJECT_DIR / "index/manifest.json")
        search_question = official_query(question, private)
        results, tokens = search(search_question, chunks, bm25)
        extra = cross_module_enrich(search_question, results, chunks, bm25)
        if extra:
            results = results + extra
        context = combine_context(build_context(results), private)
        if results:
            answer = ask_llm(make_prompt(question, context), provider, config)
            if not answer.ok:
                diagnose("answer", answer)
                print(f"  {RD}回答失败（{answer.error}），请检查配置或稍后重试{RS}")
                sys.exit(1)
            answer = answer.require_content()
            display_answer(answer)
            print(_sep())
            kb_sources = list(dict.fromkeys(r["source"] for r in results[:3]))
            for src in kb_sources:
                print(f"  {CY}📎 {src}{RS}")
            if not args.no_save_note:
                _save_note_safe(lambda p: print(f"  {DM}📝 已保存笔记：{p}{RS}"),
                                question, answer, kb_sources, tokens,
                                provider_name=provider, config=config)
        else:
            print(f"{DM}本地档案无匹配，越过博丽结界搜索网络...{RS}")
            web_results = web_search_sap(question)
            if web_results:
                answer = ask_llm(make_web_prompt(question, web_results), provider, config)
                if not answer.ok:
                    diagnose("answer", answer)
                    print(f"  {RD}回答失败（{answer.error}），请检查配置或稍后重试{RS}")
                    sys.exit(1)
                answer = answer.require_content()
                display_answer(answer)
                print(_sep())
                print_web_sources(web_results)
                web_sources = [f"{r.get('title','')} — {r.get('href','')}" for r in web_results]
                if not args.no_save_note:
                    _save_note_safe(lambda p: print(f"  {DM}📝 {p.name}{RS}"),
                                    question, answer, web_sources, tokens)
            else:
                answer = ask_llm(make_prompt(question, ""), provider, config)
                if not answer.ok:
                    diagnose("answer", answer)
                    print(f"  {RD}回答失败（{answer.error}），请检查配置或稍后重试{RS}")
                    sys.exit(1)
                answer = answer.require_content()
                display_answer(answer)
                print(_sep())
                print(f"  {YL}⚠ 本地档案无匹配，网络搜索也没命中，暂按 SF 训练知识回答{RS}")
                if not args.no_save_note:
                    _save_note_safe(lambda p: print(f"  {DM}📝 {p.name}{RS}"),
                                    question, answer, [], tokens)
        remember_sap_capture(question, answer, "sap-assistant-cli")
        return

    # 交互模式 — TUI
    run_tui(chunks, bm25, provider, config)


if __name__ == "__main__":
    main()
