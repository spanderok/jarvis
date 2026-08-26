# /// script
# requires-python = ">=3.10,<3.13"
# dependencies = ["chromadb"]
# ///
"""A recall command backed by a real vector store.

    memory.d/recall.example.py "what did we decide about the deploys"

Copy to memory.d/recall.py, point it at your collection, switch `enabled` on in
config/memory.toml. Chroma here because it needs no server; the shape is the
same for Qdrant, LanceDB or sqlite-vec - a query in, plain text out.

The contract is the whole interface:
    argv[1]   the question, exactly as transcribed
    stdout    the context, plain text, no markup - it goes into a prompt
    exit 0    even when nothing was found; print nothing instead

Everything here fails quiet. A store that is down must cost an ordinary answer,
never an error read out loud - so the exception path prints nothing and exits 0.
"""

import os
import sys

DB_PATH = os.environ.get("JARVIS_CHROMA_PATH", os.path.expanduser("~/.chroma"))
COLLECTION = os.environ.get("JARVIS_CHROMA_COLLECTION", "notes")

# Three passages is about as much as a three-sentence spoken answer can rest on.
# More of them mostly buys the model something else to talk about instead.
TOP_K = int(os.environ.get("JARVIS_CHROMA_TOP_K", "3"))

# Chroma returns a distance, not a score: smaller is closer. Above this the
# passage matched a word rather than the question, and a confidently wrong note
# read out loud is worse than "I do not know".
MAX_DISTANCE = float(os.environ.get("JARVIS_CHROMA_MAX_DISTANCE", "0.75"))


def main() -> int:
    question = " ".join(sys.argv[1:]).strip()
    if not question:
        return 0
    try:
        import chromadb

        client = chromadb.PersistentClient(path=DB_PATH)
        hits = client.get_collection(COLLECTION).query(
            query_texts=[question], n_results=TOP_K,
            include=["documents", "distances"])
    except Exception:
        return 0

    documents = (hits.get("documents") or [[]])[0]
    distances = (hits.get("distances") or [[]])[0]
    for text, distance in zip(documents, distances):
        if distance is not None and distance > MAX_DISTANCE:
            continue
        line = " ".join((text or "").split())
        if line:
            print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
