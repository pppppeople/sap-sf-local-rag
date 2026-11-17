"""
SF Knowledge Base - Index Builder
从 SAP_SF_Docs 提取所有 PDF 文本，分块，保存为 BM25 索引
"""

import sys
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
import os
import json
import re
import pickle
from pathlib import Path
from pypdf import PdfReader
from rank_bm25 import BM25Okapi

def _detect_docs_dir() -> Path:
    """Auto-detect PDF docs location.

    Priority:
    1. Project-internal docs/ folder (if it contains any PDFs)
    2. External ~/Documents/SAP_SF_Docs (default for most users)
    """
    internal = Path(__file__).parent / "docs"
    if internal.exists() and any(internal.rglob("*.pdf")):
        return internal
    return Path.home() / "Documents" / "SAP_SF_Docs"

DOCS_DIR = _detect_docs_dir()
INDEX_DIR = Path(__file__).parent / "index"
CHUNK_SIZE = 600     # words per chunk
CHUNK_OVERLAP = 100  # word overlap between chunks
MIN_CHUNK_WORDS = 30 # discard chunks shorter than this (near-empty PDF pages)

MODULE_NAMES = {
    "01_Onboarding": "Onboarding",
    "02_Platform": "Platform",
    "03_Employee_Central": "Employee Central",
    "04_Learning": "Learning",
    "05_Recruiting": "Recruiting",
    "06_Performance_Talent": "Performance & Talent",
    "07_Compensation": "Compensation",
    "08_Career_Succession": "Career & Succession",
    "09_Work_Zone": "Work Zone",
    "10_Joule_AI": "Joule / AI",
}


def extract_pdf_text(pdf_path: Path) -> str:
    try:
        reader = PdfReader(str(pdf_path))
        pages = []
        for page in reader.pages:
            text = page.extract_text()
            if text:
                pages.append(text.strip())
        return "\n\n".join(pages)
    except Exception as e:
        print(f"  [错误] {pdf_path.name}: {e}")
        return ""


def clean_text(text: str) -> str:
    # 合并多余空白，清理常见 PDF 噪声
    text = re.sub(r'\n{3,}', '\n\n', text)
    text = re.sub(r'[ \t]{2,}', ' ', text)
    text = re.sub(r'(?<!\n)\n(?!\n)', ' ', text)  # 单换行转空格
    return text.strip()


def chunk_text(text: str, source: str, module: str) -> list[dict]:
    words = text.split()
    chunks = []
    start = 0
    idx = 0
    while start < len(words):
        end = start + CHUNK_SIZE
        chunk_words = words[start:end]
        chunk_text = " ".join(chunk_words)
        if len(chunk_words) < MIN_CHUNK_WORDS:
            start += CHUNK_SIZE - CHUNK_OVERLAP
            continue
        chunks.append({
            "id": f"{source}::{idx}",
            "source": source,
            "module": module,
            "text": chunk_text,
        })
        idx += 1
        start += CHUNK_SIZE - CHUNK_OVERLAP
    return chunks


def tokenize(text: str) -> list[str]:
    return text.lower().split()


def build():
    INDEX_DIR.mkdir(exist_ok=True)

    all_chunks = []
    pdf_files = sorted(DOCS_DIR.rglob("*.pdf"))
    total = len(pdf_files)

    print(f"找到 {total} 个 PDF 文件，开始提取...\n")

    for i, pdf_path in enumerate(pdf_files, 1):
        # 确定模块
        module_folder = pdf_path.parent.name
        module = MODULE_NAMES.get(module_folder, module_folder)
        source = f"{module} / {pdf_path.stem}"

        print(f"[{i}/{total}] {pdf_path.name}", end=" ", flush=True)

        raw = extract_pdf_text(pdf_path)
        if not raw:
            print("→ 跳过（空）")
            continue

        cleaned = clean_text(raw)
        chunks = chunk_text(cleaned, source, module)
        all_chunks.extend(chunks)
        print(f"→ {len(chunks)} chunks")

    print(f"\n共 {len(all_chunks)} 个文本块，构建 BM25 索引...")

    if not all_chunks:
        print("没有可索引的内容，请先把 PDF 放入：")
        print(f"  {DOCS_DIR}")
        return

    corpus = [tokenize(c["text"]) for c in all_chunks]
    bm25 = BM25Okapi(corpus)

    # 保存
    chunks_path = INDEX_DIR / "chunks.json"
    bm25_path = INDEX_DIR / "bm25.pkl"

    with open(chunks_path, "w", encoding="utf-8") as f:
        json.dump(all_chunks, f, ensure_ascii=False, indent=2)

    with open(bm25_path, "wb") as f:
        pickle.dump(bm25, f)

    print(f"\n索引已保存到 {INDEX_DIR}/")
    print(f"  chunks.json : {len(all_chunks)} 条")
    print(f"  bm25.pkl    : BM25 模型")

    # ── 向量索引（可选，需要 sentence-transformers）──────────────────────────
    _build_vector_index(all_chunks, INDEX_DIR)

    print("\n可以运行 python3 ask.py 开始提问了。")


def _build_vector_index(chunks: list, index_dir: Path) -> None:
    """Generate sentence embeddings for all chunks and save as embeddings.npy.

    Uses paraphrase-multilingual-MiniLM-L12-v2 (multilingual, CPU-friendly, ~420MB).
    Skipped gracefully if sentence-transformers is not installed.
    """
    try:
        from sentence_transformers import SentenceTransformer
        import numpy as np
    except ImportError:
        print("\n  提示：可安装语义检索增强包，让搜索结果更准确（可选，约需下载 2GB）")
        pip_cmd = "pip" if sys.platform == "win32" else "pip3"
        print(f"  {pip_cmd} install -r requirements-semantic.txt")
        return

    print("\n少女祈祷中，正在构建语义检索索引（首次运行需下载约 420MB 模型，请耐心等待）...")

    model = SentenceTransformer('paraphrase-multilingual-MiniLM-L12-v2')

    # 每个 chunk 只取前 512 个词，避免超出模型最大长度
    texts = [" ".join(c["text"].split()[:512]) for c in chunks]

    embeddings = model.encode(
        texts,
        batch_size=64,
        show_progress_bar=True,
        normalize_embeddings=True,   # L2 归一化，后续直接点积 = 余弦相似度
        convert_to_numpy=True,
    )

    out_path = index_dir / "embeddings.npy"
    np.save(str(out_path), embeddings)
    size_mb = embeddings.nbytes // 1024 // 1024
    print(f"  embeddings.npy: {embeddings.shape}，{size_mb} MB")


if __name__ == "__main__":
    build()
