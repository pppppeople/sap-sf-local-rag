#!/usr/bin/env python3
"""
SAP SF Knowledge Base Hook
UserPromptSubmit hook: 检测 SAP 关键词 → 混合检索（BM25 + 向量语义）→ 注入上下文
"""
import sys
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
import json
import os
import pickle
import warnings
from pathlib import Path
from knowledge import KnowledgeStore, combine_context, official_query

warnings.filterwarnings("ignore")

SAP_KEYWORDS = {
    'sap', 'sf', 'successfactors', 'success factors',
    'onboarding', 'offboarding',
    'employee central', 'ec ', ' ec,', ' ec.',
    'recruiting', 'rcm', 'learning', 'lms',
    'compensation', 'performance', 'succession', 'career',
    'joule', 'work zone', 'workzone',
    'mdf', 'rbp', 'bcui', 'odata',
    'hris', 'hcm', 'btp', 'ias', 'ips',
    'bizx', 'people profile', 'position management',
    'time management', 'payroll', 'benefits',
    'talent intelligence', 'skills',
}

INDEX_DIR = Path(__file__).parent / "index"
TOP_K = 8
MAX_CONTEXT_CHARS = 12000

EXCLUDED_SOURCES = {
    "Onboarding / SF_ONB_Impl",
    "Onboarding / SF_ONB_Admin_en",
    "Onboarding / SF_ONB1_Compliance_Form_Updates",
    "Onboarding / SF_ONB_Compliance_Form_Updates",
}


def is_sap_related(text: str) -> bool:
    lower = text.lower()
    return any(kw in lower for kw in SAP_KEYWORDS)


def hybrid_search(message: str, chunks: list, bm25, embeddings) -> list:
    sys.path.insert(0, str(Path(__file__).parent / 'lib'))
    from sf_tokenize import tokenize_query
    tokens = tokenize_query(message) or message.lower().split()
    bm25_scores = bm25.get_scores(tokens)

    if embeddings is not None:
        try:
            import numpy as np
            from sentence_transformers import SentenceTransformer
            model = SentenceTransformer('paraphrase-multilingual-MiniLM-L12-v2')
            query_emb = model.encode([message], normalize_embeddings=True)[0]
            with np.errstate(divide='ignore', over='ignore', invalid='ignore'):
                vector_scores = embeddings @ query_emb

            bm25_max = bm25_scores.max()
            bm25_norm = bm25_scores / bm25_max if bm25_max > 0 else bm25_scores
            vec_norm = (vector_scores + 1) / 2
            combined = 0.4 * bm25_norm + 0.6 * vec_norm

            top = np.argsort(combined)[::-1][:TOP_K * 2]
            return [
                chunks[i] for i in top
                if combined[i] > 0.3 and chunks[i]["source"] not in EXCLUDED_SOURCES
            ][:TOP_K]
        except Exception:
            pass

    # BM25 fallback
    top = sorted(range(len(bm25_scores)), key=lambda i: bm25_scores[i], reverse=True)[:TOP_K * 2]
    return [
        chunks[i] for i in top
        if bm25_scores[i] > 0 and chunks[i]["source"] not in EXCLUDED_SOURCES
    ][:TOP_K]


def main():
    try:
        data = json.load(sys.stdin)
        message = data.get("message", "") or data.get("prompt", "")
        if not message or not is_sap_related(message):
            sys.exit(0)

        chunks_path = INDEX_DIR / "chunks.json"
        bm25_path = INDEX_DIR / "bm25.pkl"
        if not chunks_path.exists() or not bm25_path.exists():
            sys.exit(0)

        with open(chunks_path, encoding="utf-8") as f:
            chunks = json.load(f)
        with open(bm25_path, "rb") as f:
            from rank_bm25 import BM25Okapi
            bm25 = pickle.load(f)

        # 尝试加载向量索引
        embeddings = None
        emb_path = INDEX_DIR / "embeddings.npy"
        if emb_path.exists():
            try:
                import numpy as np
                embeddings = np.load(str(emb_path))
            except Exception:
                pass

        notes_dir = Path(os.environ.get("SAP_SF_NOTES_DIR", Path.home() / ".local/share/sap-sf-assistant/notes"))
        private = KnowledgeStore(notes_dir).retrieve(message,
            manifest=Path(__file__).parent / "index/manifest.json")
        results = hybrid_search(official_query(message, private), chunks, bm25, embeddings)

        if not results:
            sys.exit(0)

        parts = []
        total = 0
        for r in results:
            block = f"[{r['source']}]\n{r['text']}"
            if total + len(block) > MAX_CONTEXT_CHARS:
                break
            parts.append(block)
            total += len(block)

        context = "\n\n---\n\n".join(parts)
        context = combine_context(context, private)
        output = {
            "hookSpecificOutput": {
                "hookEventName": "UserPromptSubmit",
                "additionalContext": f"[SAP SF 知识库自动检索]\n\n{context}"
            }
        }
        print(json.dumps(output, ensure_ascii=False))

    except Exception:
        sys.exit(0)


if __name__ == "__main__":
    main()
