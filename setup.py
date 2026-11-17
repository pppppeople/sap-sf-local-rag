"""
SAP SF Assistant — 首次配置脚本
自动检测操作系统，配置路径，可选写入 Claude Code hook。

用法：
    python3 setup.py          # 交互式配置
    python3 setup.py --hook   # 同时写入 Claude Code hook
    python3 setup.py --check  # 仅检查当前环境，不写入任何文件
"""

from __future__ import annotations

import sys
import platform
import shutil
import json
import subprocess
import argparse
from pathlib import Path

# ── 颜色 ──────────────────────────────────────────────────────────────────────
IS_WIN = platform.system() == "Windows"  # 初始值，main() 里可能被覆盖


def detect_os(override: str | None = None) -> bool:
    """Return True if Windows, False if Mac/Linux.
    Prompts the user to choose manually if the system cannot be determined
    or if override is provided.
    """
    sys_name = platform.system()

    if override:
        name = override.lower()
        if name in ("win", "windows"):
            return True
        if name in ("mac", "macos", "darwin", "linux"):
            return False
        print(WRN(f"未知 --os 值 '{override}'，将自动检测"))

    if sys_name == "Windows":
        return True
    if sys_name in ("Darwin", "Linux"):
        return False

    # 无法识别（如 WSL、Cygwin 等边缘情况）— 提示手动选择
    print(WRN(f"无法自动识别操作系统（platform.system() = '{sys_name}'）"))
    print("请手动选择：")
    print("  1. Windows")
    print("  2. Mac / Linux")
    while True:
        choice = input("输入 1 或 2：").strip()
        if choice == "1":
            return True
        if choice == "2":
            return False
        print("  请输入 1 或 2")

def _c(code: str, text: str) -> str:
    return f"\033[{code}m{text}\033[0m"

OK  = lambda t: _c("32", f"✓ {t}")
ERR = lambda t: _c("31", f"✗ {t}")
WRN = lambda t: _c("33", f"! {t}")
HD  = lambda t: _c("1;36", t)

# ── 路径辅助 ──────────────────────────────────────────────────────────────────

def to_posix(path) -> str:
    """Convert Windows absolute path to POSIX (for Claude Code hooks on Windows)."""
    s = str(path).replace("\\", "/")
    # Convert a drive prefix to the corresponding POSIX drive component.
    if len(s) >= 2 and s[1] == ":":
        s = "/" + s[0].lower() + s[2:]
    return s


def find_bin(name: str, extra_candidates: list[str] | None = None) -> str | None:
    """Find a binary by name, falling back to a list of explicit paths."""
    found = shutil.which(name)
    if found:
        return found
    for c in (extra_candidates or []):
        p = Path(c)
        if p.exists():
            return str(p)
    return None


def find_python(is_win: bool = IS_WIN) -> tuple[str | None, str]:
    """Return (python_executable, version_string)."""
    candidates = ["python3", "python"] if not is_win else ["python", "python3"]
    extra: list[str] = []
    if is_win:
        home = Path.home()
        extra += [
            str(home / "AppData/Local/Programs/Python/Python312/python.exe"),
            str(home / "AppData/Local/Programs/Python/Python311/python.exe"),
        ]
    for cmd in candidates:
        exe = shutil.which(cmd)
        if exe:
            try:
                r = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=5)
                ver = (r.stdout + r.stderr).strip()
                if r.returncode == 0:
                    return exe, ver
            except Exception:
                pass
    for path in extra:
        p = Path(path)
        if p.exists():
            try:
                r = subprocess.run([str(p), "--version"], capture_output=True, text=True, timeout=5)
                ver = (r.stdout + r.stderr).strip()
                if r.returncode == 0:
                    return str(p), ver
            except Exception:
                pass
    return None, ""


def find_claude(is_win: bool = IS_WIN) -> str | None:
    candidates = []
    if not is_win:
        candidates = [
            str(Path.home() / ".npm-global/bin/claude"),
        ]
    return find_bin("claude", candidates)


def find_nlm(is_win: bool = IS_WIN) -> str | None:
    candidates: list[str] = []
    if not is_win:
        candidates += [
            str(Path.home() / ".local/bin/nlm"),
            str(Path.home() / "anaconda3/envs/ima-mcp/bin/nlm"),
            str(Path.home() / "miniconda3/envs/ima-mcp/bin/nlm"),
        ]
    return find_bin("nlm", candidates) or find_bin("nlm.exe", candidates)


# ── 配置文件 ──────────────────────────────────────────────────────────────────

CONFIG_PATH = Path.home() / ".sf-assistant-config.json"


def load_config() -> dict:
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH, encoding="utf-8") as f:
            return json.load(f)
    return {"default_provider": "claude-code", "providers": {}}


def save_config(cfg: dict):
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


# ── Claude Code hook ──────────────────────────────────────────────────────────

CLAUDE_SETTINGS = Path.home() / ".claude" / "settings.json"


def install_hook(python_exe: str, script_dir: Path):
    """Add UserPromptSubmit hook to ~/.claude/settings.json."""
    hook_script = script_dir / "kb_hook.py"
    # Claude Code runs hooks via bash — always use POSIX paths
    cmd = f"{to_posix(python_exe)} {to_posix(hook_script)}"

    settings: dict = {}
    if CLAUDE_SETTINGS.exists():
        with open(CLAUDE_SETTINGS, encoding="utf-8") as f:
            settings = json.load(f)

    hooks = settings.setdefault("hooks", {})
    ups_list = hooks.setdefault("UserPromptSubmit", [])

    # Check if already configured
    for entry in ups_list:
        for h in entry.get("hooks", []):
            if "kb_hook.py" in h.get("command", ""):
                print(WRN("Hook 已存在，跳过写入"))
                return

    ups_list.append({
        "hooks": [{
            "type": "command",
            "command": cmd,
            "timeout": 15,
            "statusMessage": "检索 SAP 知识库...",
        }]
    })

    CLAUDE_SETTINGS.parent.mkdir(parents=True, exist_ok=True)
    with open(CLAUDE_SETTINGS, "w", encoding="utf-8") as f:
        json.dump(settings, f, ensure_ascii=False, indent=2)

    print(OK(f"Hook 已写入 {CLAUDE_SETTINGS}"))
    print(f"   命令: {cmd}")
    print("   重启 Claude Code 后生效")



# ── sap 快捷命令 ──────────────────────────────────────────────────────────────

def _install_alias(python_exe: str, script_dir: Path, is_win: bool):
    """Write `sap` shortcut to shell config (Mac/Linux) or PowerShell profile (Windows)."""
    ask_py = script_dir / "ask.py"

    if is_win:
        # Windows: write a function to PowerShell profile
        try:
            r = subprocess.run(
                ["powershell", "-NoProfile", "-Command", "echo $PROFILE"],
                capture_output=True, text=True, timeout=5
            )
            profile_path = Path(r.stdout.strip())
        except Exception:
            print(WRN("无法获取 PowerShell profile 路径，跳过 sap 快捷命令配置"))
            return

        alias_line = f'\nfunction sap {{ & "{python_exe}" "{ask_py}" @args }}\n'
        marker = "# sap-sf-assistant"

        profile_path.parent.mkdir(parents=True, exist_ok=True)
        existing = profile_path.read_text(encoding="utf-8") if profile_path.exists() else ""
        if marker in existing:
            print(WRN("sap 快捷命令已存在，跳过"))
            return
        with open(profile_path, "a", encoding="utf-8") as f:
            f.write(f"\n{marker}{alias_line}")
        print(OK(f"sap 快捷命令已写入 {profile_path}"))
        print("   重启 PowerShell 后生效，或运行：. $PROFILE")
    else:
        # Mac/Linux: write alias to .zshrc and/or .bash_profile
        alias_line = f"alias sap='cd \"{script_dir}\" && {python_exe} ask.py'"
        marker = "# sap-sf-assistant"

        targets = []
        zshrc = Path.home() / ".zshrc"
        bashrc = Path.home() / ".bash_profile"
        if zshrc.exists() or shutil.which("zsh"):
            targets.append(zshrc)
        if bashrc.exists():
            targets.append(bashrc)
        if not targets:
            targets.append(zshrc)  # fallback: create .zshrc

        written = []
        for rc in targets:
            existing = rc.read_text(encoding="utf-8") if rc.exists() else ""
            if marker in existing:
                continue
            with open(rc, "a", encoding="utf-8") as f:
                f.write(f"\n{marker}\n{alias_line}\n")
            written.append(rc)

        if written:
            print(OK(f"sap 快捷命令已写入 {', '.join(str(p) for p in written)}"))
            print("   新开终端即可使用，或运行：source ~/.zshrc")
        else:
            print(WRN("sap 快捷命令已存在，跳过"))


# ── 交互补充 ──────────────────────────────────────────────────────────────────

def _prompt_missing(found: str | None, name: str, required: bool,
                    hint: str = "", install_url: str = "") -> str | None:
    """If `found` is None, ask user to enter path, skip, or see install link."""
    if found:
        return found

    label = f"{name}{'（必需）' if required else '（可选）'}"
    print(f"\n  {WRN(f'{label} 未自动检测到')}")

    options = ["1. 手动输入安装路径"]
    if not required:
        options.append("2. 跳过（不使用此功能）")
    if install_url:
        options.append(f"{'3' if not required else '2'}. 查看安装说明")

    for opt in options:
        print(f"     {opt}")

    while True:
        choice = input("  请选择：").strip()

        if choice == "1":
            path = input(f"  路径（{hint}）：").strip().strip('"').strip("'")
            p = Path(path).expanduser()
            if p.exists():
                print(f"  {OK(str(p))}")
                return str(p)
            # 用户输入的是命令名（如 python3），尝试 which
            found_via_which = shutil.which(path)
            if found_via_which:
                print(f"  {OK(found_via_which)}")
                return found_via_which
            print(f"  {ERR(f'路径不存在：{path}，请重新输入')}")
            continue

        if choice == "2" and not required:
            print(f"  已跳过 {name}")
            return None

        # install url option
        idx = "3" if not required else "2"
        if choice == idx and install_url:
            print(f"  请在浏览器中打开：{install_url}")
            print(f"  安装完成后重新运行此脚本，或选择 1 手动输入路径")
            continue

        print("  请输入有效选项")


# ── 主流程 ────────────────────────────────────────────────────────────────────

def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description="SAP SF Assistant 配置脚本")
    parser.add_argument("--hook",  action="store_true", help="同时写入 Claude Code hook")
    parser.add_argument("--check", action="store_true", help="仅检查环境，不写入文件")
    parser.add_argument("--os",    metavar="OS", help="手动指定系统：win 或 mac")
    args = parser.parse_args()

    print(f"\n{HD('SAP SF Assistant — 环境配置')}")

    is_win  = detect_os(args.os)
    os_name = "Windows" if is_win else "macOS / Linux"
    print(f"  操作系统：{os_name}\n")

    # ── 检测各依赖 ────────────────────────────────────────────────────────────
    python_exe, python_ver = find_python(is_win)
    claude_exe = find_claude(is_win)
    nlm_exe    = find_nlm(is_win)

    print(f"{'Python':<14} {OK(python_ver) if python_exe else ERR('未找到 — 请先安装 Python 3.9+')}")
    print(f"{'Claude Code':<14} {OK(claude_exe) if claude_exe else WRN('未找到（claude-code provider 不可用）')}")
    print(f"{'nlm (NotebookLM)':<14} {OK(nlm_exe) if nlm_exe else WRN('未找到（notebooklm provider 不可用）')}")

    if args.check:
        print("\n（--check 模式，不写入任何文件）")
        if not python_exe:
            print(ERR("Python 未找到，请先安装：https://www.python.org/downloads/"))
            sys.exit(1)
        return

    # ── 未找到时提示手动补充 ──────────────────────────────────────────────────
    python_exe  = _prompt_missing(python_exe,  "Python",      required=True,
                                  hint="python3 或你自行配置的可执行文件路径",
                                  install_url="https://www.python.org/downloads/")
    claude_exe  = _prompt_missing(claude_exe,  "Claude Code", required=False,
                                  hint="claude 或完整路径，如 ~/.npm-global/bin/claude")
    nlm_exe     = _prompt_missing(nlm_exe,     "nlm (NotebookLM)", required=False,
                                  hint="nlm 或完整路径，如 ~/anaconda3/envs/ima-mcp/bin/nlm")

    # ── 写配置文件 ────────────────────────────────────────────────────────────
    cfg = load_config()
    platform_cfg = cfg.setdefault("platform", {})
    platform_cfg["os"]         = platform.system()
    platform_cfg["python_exe"] = python_exe

    if claude_exe:
        platform_cfg["claude_bin"] = claude_exe

    if nlm_exe:
        platform_cfg["nlm_bin"] = nlm_exe

    # Ensure docs dir exists
    docs_dir = Path.home() / "Documents" / "SAP_SF_Docs"
    for sub in ["01_Onboarding", "02_Platform", "03_Employee_Central",
                "04_Learning", "05_Recruiting", "06_Performance_Talent",
                "07_Compensation", "08_Career_Succession", "09_Work_Zone", "10_Joule_AI"]:
        (docs_dir / sub).mkdir(parents=True, exist_ok=True)

    save_config(cfg)
    print(f"\n{OK(f'配置已写入 {CONFIG_PATH}')}")
    print(f"  PDF 目录已建好：{docs_dir}")

    # ── Claude Code hook ──────────────────────────────────────────────────────
    if args.hook:
        print()
        install_hook(python_exe, Path(__file__).parent)
    else:
        hook_cmd = f"python3 setup.py --hook" if not IS_WIN else f"python setup.py --hook"
        print(f"\n如需配置 Claude Code 自动检索 hook，运行：{hook_cmd}")

    # ── sap 快捷命令 ──────────────────────────────────────────────────────────
    _install_alias(python_exe, Path(__file__).parent, is_win)

    print(f"\n{HD('配置完成！')}")
    pip_cmd = f"{python_exe} -m pip"
    print(f"  下一步：安装依赖")
    print(f"  {pip_cmd} install -r requirements.txt")
    print(f"  可选语义检索增强：{pip_cmd} install -r requirements-semantic.txt")
    print(f"  然后把 SAP PDF 放入 {docs_dir}/<模块文件夹>，运行 python3 build_index.py 建立索引")


if __name__ == "__main__":
    main()
