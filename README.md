# SAP SuccessFactors Local RAG

Bring-your-own-docs local RAG for SAP SuccessFactors — ask questions over your own SAP Help PDFs, with Claude/OpenAI/Gemini/DeepSeek/Kimi and more.

This repository contains code and fictional test fixtures. It includes **no SAP official documents, customer records, private notes or prebuilt index**. Examples are fictional. SAP and SuccessFactors are trademarks of their respective owners; this is an independent community tool.

## Quickstart

Use Python 3.11+ for this export (the knowledge module uses modern union types).
From the extracted repository directory:

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements-dev.txt
mkdir -p docs/01_Onboarding
```

Download only documents you are entitled to use from the [SAP Help Portal](https://help.sap.com/). Put your own PDFs under `docs/`, optionally grouped into module directories such as `01_Onboarding` or `03_Employee_Central`. Do not commit or redistribute them. Then:

```sh
python build_index.py
python ask.py --setup
python ask.py --provider deepseek "How are Onboarding permissions configured?"
python ask.py
```

The setup prompt stores your provider API credential in your own `~/.sf-assistant-config.json`; keep that file private. `--setup` is the interactive provider configuration, not the repository's optional `setup.py` integration helper. Claude Code and Codex providers require their separately installed, authenticated CLIs. Other adapters include Anthropic, OpenAI-compatible providers, Gemini, DeepSeek, Kimi, MiniMax, Qwen and Doubao; availability depends on your own credentials and provider service. `/provider`, `/search`, `/sources`, `/help` and `/exit` are available in the interactive CLI. Semantic retrieval is optional: install `requirements-semantic.txt` before building a vector index. Its first use can download model weights; none are bundled. The core and development requirements use BM25 without the semantic stack.

## Data and privacy

PDF extraction, BM25 indexing and retrieval run locally. Answer generation sends your question and selected excerpts to your chosen model provider or CLI: local-first does not mean offline model inference. The index is stored in `index/`; optional notes default to `~/.local/share/sap-sf-assistant/notes`, configurable with `SAP_SF_NOTES_DIR`. Nothing reads a sibling bot or private messaging project. Capture automation is disabled unless you explicitly set `SAP_CAPTURE_DRAFT_SCRIPT` to your own compatible adapter. NotebookLM module IDs start empty; configure your own notebooks locally before selecting that adapter.

Model adapters return a structured `Result` (`ok`, `content`, `error`, `retryable`). Failed model output is rejected before display, conversation history, capture or note merge. Diagnostics contain error codes/types rather than upstream prompt or response text. DOCX updates are validated in temporary files beside the destination before replacement; failed conversion or merge preserves the existing note. NotebookLM without a user-configured notebook returns `configuration_missing`.

The `.env.example` lists optional environment names with blank values; the application does not automatically load this file. Use your shell or provider setup prompt. `setup.py` is an optional integration helper that can edit shell/Claude configuration when invoked; it is not needed for the quickstart. No such integration runs during tests. Invoke Python entrypoints through `python`; the integration helper discovers executables through PATH or user-home locations and prompts for a custom path when needed. It contains no fixed host installation paths.

For custom CLI locations, set `SAP_SF_CODEX_PATH` or `SAP_SF_NLM_PATH` to candidate executable paths separated by `os.pathsep` (`:` on POSIX, `;` on Windows); the first existing candidate wins. Saved `platform.codex_bin`/`platform.nlm_bin` settings take precedence, followed by these environment candidates, PATH lookup, and (for NotebookLM) user-home install locations; otherwise the command name is used. No fixed drive or application installation directory is assumed.

## Checks and limitations

```sh
python -m pytest -q
python eval/run_retrieval_eval.py
python eval/smoke_imports.py
```

Tests use fictional fixtures, not a licensed SAP corpus or tenant. The export's retrieval gate runs local chunking/tokenization regressions; it does not include the private workspace's 200-scenario corpus or certify answer accuracy. The private DOCX style template is omitted; note generation uses the existing default-template fallback. Retrieved facts still need source/version review and tenant validation. This export includes the portable changes through source revision `6547149`: failure handling, layered dependencies, read-only import smoke checks, and removal of the unused `query_context` wrapper. Call `query_context_data` for local retrieval. The sibling-bot retrieval adapter and its tests are excluded; no private host, friend broker, SAP tenant or mail wiring is bundled.

Real model calls and capture-adapter delivery require your own accounts/configuration and have not been accepted in this sync. Clean-clone installation, optional semantic model download, actual document retrieval quality and tenant behavior remain independently unverified. See [SCAN.md](SCAN.md) for the audit scope.

## 中文简介

自带 SAP Help PDF 的本地检索问答工具：自行下载有权使用的文档，放入 docs/，建立索引后选择模型提问。仓库不附带 SAP 官方文档、客户材料或私人笔记；模型问答可能将选中摘录发送给所选服务。代码采用 MIT，依赖保留各自许可证。
