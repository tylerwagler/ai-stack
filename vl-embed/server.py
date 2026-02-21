"""
vl-embed: Rerank proxy for Qwen3-VL-Reranker-2B on vLLM.

Formats query/document pairs with the proper chat template and forwards
to vLLM's /classify endpoint. Returns Open WebUI-compatible rerank responses.
"""

import asyncio
import logging
import os
import time
from contextlib import asynccontextmanager
from typing import List, Optional, Union

import httpx
import uvicorn
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)
logger = logging.getLogger("vl-embed")

PORT = int(os.environ.get("PORT", "8014"))
VLLM_RERANKER_URL = os.environ.get("VLLM_RERANKER_URL", "http://10.20.10.10:8016")

# ---------- reranker prompt template ----------
SYSTEM_PROMPT = (
    'Judge whether the Document meets the requirements based on the '
    'Query and the Instruct provided. Note that the answer can only '
    'be "yes" or "no".'
)
DEFAULT_INSTRUCTION = (
    "Given a search query, retrieve relevant candidates that answer the query."
)


def format_rerank_prompt(query: str, document: str, instruction: str = DEFAULT_INSTRUCTION) -> str:
    """Format a query+document pair using the Qwen3-VL-Reranker chat template."""
    return (
        f"<|im_start|>system\n{SYSTEM_PROMPT}<|im_end|>\n"
        f"<|im_start|>user\n"
        f"<Instruct>: {instruction}"
        f"<Query>:{query}\n"
        f"<Document>:{document}<|im_end|>\n"
        f"<|im_start|>assistant\n"
    )


# ---------- lifespan ----------
http_client: Optional[httpx.AsyncClient] = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global http_client
    http_client = httpx.AsyncClient(timeout=120.0)
    logger.info("Rerank proxy ready — backend: %s", VLLM_RERANKER_URL)
    yield
    await http_client.aclose()
    logger.info("Shutting down")


app = FastAPI(title="vl-embed", lifespan=lifespan)


# ---------- request / response schemas ----------
class RerankRequest(BaseModel):
    model: str = "Qwen3-VL-Reranker-2B"
    query: str
    documents: Union[List[str], List[dict]]
    top_n: Optional[int] = None


class RerankResult(BaseModel):
    index: int
    relevance_score: float
    document: Optional[dict] = None


class RerankResponse(BaseModel):
    model: str
    results: List[RerankResult]


# ---------- endpoints ----------
@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/v1/rerank", response_model=RerankResponse)
async def rerank(req: RerankRequest):
    # Extract text from documents
    docs: List[str] = []
    for d in req.documents:
        if isinstance(d, str):
            docs.append(d)
        elif isinstance(d, dict) and "text" in d:
            docs.append(d["text"])
        else:
            raise HTTPException(status_code=400, detail="documents must be strings or {text: ...}")

    if not docs:
        raise HTTPException(status_code=400, detail="documents is empty")

    # Format each query+doc pair with the reranker template
    prompts = [
        {"prompt": format_rerank_prompt(req.query, doc)}
        for doc in docs
    ]

    t0 = time.time()

    # Call vLLM /classify endpoint — one request per doc
    # (vLLM classify expects {"model": ..., "input": ...} like embeddings)
    results = []
    for i, p in enumerate(prompts):
        resp = await http_client.post(
            f"{VLLM_RERANKER_URL}/classify",
            json={
                "model": "Qwen/Qwen3-VL-Reranker-2B",
                "input": p["prompt"],
            },
        )

        if resp.status_code != 200:
            logger.error("vLLM classify failed: %s %s", resp.status_code, resp.text)
            raise HTTPException(status_code=502, detail=f"vLLM error: {resp.text}")

        data = resp.json()
        # probs[0] is the relevance score per the official notebook
        probs = data["data"][0]["probs"]
        score = probs[0]
        results.append(
            RerankResult(index=i, relevance_score=score, document={"text": docs[i]})
        )

    elapsed = time.time() - t0
    logger.info("Reranked %d docs in %.2fs via vLLM", len(docs), elapsed)

    # Sort by score descending
    results.sort(key=lambda r: r.relevance_score, reverse=True)

    if req.top_n is not None:
        results = results[: req.top_n]

    return RerankResponse(model=req.model, results=results)


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="info")
