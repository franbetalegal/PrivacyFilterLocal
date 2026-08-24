"""Custom dictionary: user-editable terms to always anonymize."""

from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel

router = APIRouter()


class DictEntryIn(BaseModel):
    """A dictionary term submitted from the UI (add or update)."""
    term: str
    label: str = "OTRO"
    match: str = "smart"
    enabled: bool = True


class DictEntryPatch(BaseModel):
    """Partial update — only the provided fields change."""
    term: str | None = None
    label: str | None = None
    match: str | None = None
    enabled: bool | None = None


class DictImportIn(BaseModel):
    """A dictionary export to smart-merge into the local one."""
    terms: list[dict]


@router.get("/api/dictionary")
def api_dictionary_list() -> dict:
    """Return the current dictionary plus the canonical label suggestions."""
    from server import custom_dict

    return {
        "terms": custom_dict.get_store().export_terms(),
        "labels": list(custom_dict.CANONICAL_LABELS),
        "match_modes": list(custom_dict.MATCH_MODES),
    }


@router.post("/api/dictionary")
def api_dictionary_add(entry: DictEntryIn) -> dict:
    """Add one term (or update label/enabled if the same target exists)."""
    from server import custom_dict

    err = custom_dict.validate_entry(entry.term, entry.match)
    if err:
        raise HTTPException(status_code=400, detail=err)
    saved = custom_dict.get_store().add(
        term=entry.term, label=entry.label, match=entry.match,
        enabled=entry.enabled,
    )
    return saved.to_dict()


@router.put("/api/dictionary/{entry_id}")
def api_dictionary_update(entry_id: str, patch: DictEntryPatch) -> dict:
    """Update fields of an existing term."""
    from server import custom_dict

    fields = {k: v for k, v in patch.model_dump().items() if v is not None}
    if "term" in fields or "match" in fields:
        term = fields.get("term", "")
        match = fields.get("match", "smart")
        err = custom_dict.validate_entry(term or "x", match)
        if err:
            raise HTTPException(status_code=400, detail=err)
    updated = custom_dict.get_store().update(entry_id, **fields)
    if updated is None:
        raise HTTPException(status_code=404, detail="Término no encontrado.")
    return updated.to_dict()


@router.delete("/api/dictionary/{entry_id}")
def api_dictionary_delete(entry_id: str) -> dict:
    """Remove a term from the dictionary."""
    from server import custom_dict

    if not custom_dict.get_store().remove(entry_id):
        raise HTTPException(status_code=404, detail="Término no encontrado.")
    return {"removed": entry_id}


@router.post("/api/dictionary/import")
def api_dictionary_import(payload: DictImportIn) -> dict:
    """Smart-merge an imported dictionary (union, keep local on conflict)."""
    from server import custom_dict

    return custom_dict.get_store().import_terms(payload.terms)


@router.get("/api/dictionary/export")
def api_dictionary_export() -> Response:
    """Download the whole dictionary as a shareable JSON file."""
    from server import custom_dict

    body = json.dumps(
        {"version": 1, "terms": custom_dict.get_store().export_terms()},
        ensure_ascii=False, indent=2,
    )
    return Response(
        content=body,
        media_type="application/json",
        headers={
            "Content-Disposition": "attachment; filename=diccionario-anonimizador.json"
        },
    )
