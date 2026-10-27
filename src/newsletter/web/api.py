from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ValidationError
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from newsletter.feedback import record_thumb
from newsletter.ingest.sources import preview, subscribe, unsubscribe
from newsletter.web.deps import get_db, now_of, require_owner_json

router = APIRouter(prefix="/api", dependencies=[Depends(require_owner_json)])
NO_FEED = "No feed found"


class FeedbackIn(BaseModel):
    impression_id: int
    value: Literal["up", "down", "clear"]


class UrlIn(BaseModel):
    url: str


async def parse(request: Request, model: type[BaseModel]):
    try:
        return model.model_validate(await request.json())
    except (ValueError, ValidationError) as exc:
        raise HTTPException(422, "Invalid request body") from exc


@router.post("/feedback")
async def feedback(request: Request, session: Session = Depends(get_db)):
    body = await parse(request, FeedbackIn)
    try:
        value = await run_in_threadpool(record_thumb, session, body.impression_id, body.value,
                                        request.headers.get("user-agent"), now_of(request))
    except LookupError:
        raise HTTPException(404, "Unknown impression") from None
    return {"value": value}


@router.post("/sources/preview")
async def preview_source(request: Request):
    body = await parse(request, UrlIn)
    state = request.app.state
    cfg = await run_in_threadpool(preview, body.url, state.client, state.adapters)
    if cfg is None:
        return {"found": False, "message": NO_FEED}
    return {"found": True, "adapter": cfg.adapter, "title": cfg.title, "site_domain": cfg.site_domain,
            "preview_titles": cfg.preview_titles}


@router.post("/sources")
async def add_source(request: Request, session: Session = Depends(get_db)):
    body = await parse(request, UrlIn)
    state = request.app.state
    source = await run_in_threadpool(subscribe, session, body.url, state.client, state.adapters,
                                    now_of(request))
    if source is None:
        return JSONResponse({"message": NO_FEED}, status_code=422)
    return JSONResponse({"id": source.id, "title": source.title, "message": f"Subscribed to {source.title}"},
                        status_code=201)


@router.delete("/sources/{source_id}")
def remove_source(source_id: int, request: Request, session: Session = Depends(get_db)):
    if not unsubscribe(session, source_id, now_of(request)):
        raise HTTPException(404, "Unknown source")
    return {"ok": True}
