"""FastAPI: POST /verify（構造化 claim 群 → 商品単位の集約判定 + claim 別結果）。

起動（ローカルのみ）:  .venv/bin/uvicorn src.api:app --port 8000
カタログは環境変数 SPECSURE_SEED（既定: data/seed/specbench_v0.yaml）から読む。
"""

from __future__ import annotations

import os
from functools import lru_cache
from typing import Any, Literal

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from .claims import CLAIM_TYPES, Claim, ClaimError
from .snapshot import DEFAULT_SEED, Catalog
from .verify import VERIFIER_ID, Verifier


class ProductRef(BaseModel):
    gtin: str | None = None
    model_number: str | None = None
    name: str | None = None          # 商品名（完全一致の第三キー）


class ClaimIn(BaseModel):
    claim_id: str
    claim_type: Literal[CLAIM_TYPES]  # type: ignore[valid-type]
    value: Any
    product_ref: ProductRef = Field(default_factory=ProductRef)
    attribute: str | None = None
    unit: str | None = None
    polarity: Literal["affirm", "deny"] = "affirm"
    severity: Literal["S1", "S2", "S3"] | None = None


class VerifyRequest(BaseModel):
    claims: list[ClaimIn] = Field(min_length=1, max_length=500)


@lru_cache(maxsize=None)
def _verifier(seed_path: str) -> Verifier:
    return Verifier(Catalog.from_seed(seed_path))


def get_verifier() -> Verifier:
    return _verifier(os.environ.get("SPECSURE_SEED", str(DEFAULT_SEED)))


app = FastAPI(title="SpecSure verifier", version="1",
              description="決定論 verifier v1。モデル・外部 API は呼ばない。")


@app.get("/health")
def health() -> dict:
    v = get_verifier()
    return {"status": "ok", "verifier": VERIFIER_ID, "products": len(v.catalog.products)}


@app.post("/verify")
def verify(req: VerifyRequest) -> dict:
    try:
        claims = [Claim(**{**c.model_dump(), "product_ref": c.product_ref.model_dump(exclude_none=True)})
                  for c in req.claims]
    except ClaimError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    return get_verifier().verify(claims)
