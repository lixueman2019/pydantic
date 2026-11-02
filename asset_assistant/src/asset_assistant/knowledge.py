"""Local, page-addressable keyword retrieval. PDF contents are data, never instructions."""
import hashlib
import re
from pathlib import Path

from pypdf import PdfReader

from .contracts import document_paths


class KnowledgeService:
    def __init__(self, root: Path):
        self.documents = document_paths(root)
        self.pages = []
        self.metadata = []
        for source_id, path in self.documents.items():
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            reader = PdfReader(path)
            texts = [page.extract_text() or "" for page in reader.pages]
            version = re.search(r"\bRev(?:ision)?\.?\s+([A-Z]|\d+)\b", "\n".join(texts))
            title = str((reader.metadata or {}).get('/Title') or path.stem)
            self.metadata.append(dict(source_id=source_id, path=path.name, sha256=digest,
                                      pages=len(texts), version=version.group(0) if version else None,
                                      category="lab_pdf", license_status="unverified"))
            for page_number, text in enumerate(texts, 1):
                # Overlapping bounded passages retain literal PDF text, not model summaries.
                for offset in range(0, len(text), 1400):
                    excerpt = text[offset:offset + 1800].strip()
                    if excerpt:
                        self.pages.append(dict(
                            citation_id=f"{source_id}:{digest[:12]}:{page_number}:{offset}",
                            source_id=source_id, page=page_number, title=title, section=None,
                            excerpt=excerpt, sha256=digest,
                            version=version.group(0) if version else None))
        self.ready = all(any(p['source_id'] == s for p in self.pages) for s in self.documents)

    def search_documents(self, query: str, limit: int = 5) -> list[dict]:
        if not isinstance(query, str) or not 1 <= limit <= 5 or len(query) > 2000:
            return []
        terms = set(re.findall(r"[\w.%-]+", query.lower()))
        ranked = [(sum(term in p['excerpt'].lower() for term in terms), p) for p in self.pages]
        ranked.sort(key=lambda pair: pair[0], reverse=True)
        return [dict(p) for score, p in ranked[:limit] if score > 0]