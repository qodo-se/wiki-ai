from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, field_validator

import config as config_store

router = APIRouter(prefix="/api/v1/config", tags=["config"])

URL_FIELDS = {"ollama_url", "qdrant_url"}


class ConfigBody(BaseModel):
    ollama_url: str | None = None
    ollama_embedding_model: str | None = None
    qdrant_url: str | None = None
    qdrant_collection: str | None = None

    @field_validator("ollama_url", "qdrant_url", "ollama_embedding_model", "qdrant_collection")
    @classmethod
    def _not_blank(cls, v, info):
        if v is None:
            return v
        v = v.strip()
        if not v:
            raise ValueError(f"{info.field_name} cannot be blank")
        if info.field_name in URL_FIELDS and not (v.startswith("http://") or v.startswith("https://")):
            raise ValueError(f"{info.field_name} must start with http:// or https://")
        return v


@router.get("")
def get_config():
    return config_store.get_all_config()


@router.put("")
def put_config(body: ConfigBody):
    updates = {k: v for k, v in body.model_dump().items() if v is not None}
    try:
        return config_store.set_config(updates)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
