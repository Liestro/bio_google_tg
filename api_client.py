#!/usr/bin/env python3
import os
import json
import asyncio
from typing import Any, Dict, Optional, List

import aiohttp
from aiohttp import ClientTimeout
from aiohttp.client_exceptions import ClientError


DEFAULT_API_URL = "https://lbf7-hackaton.replit.app/ask"


def _build_headers() -> Dict[str, str]:
    headers: Dict[str, str] = {
        "User-Agent": "tg-bot",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "x-language": "en",
    }
    token_bearer = next(
        (
            os.environ.get(name)
            for name in [
                "API_TOKEN",
                "AUTH_TOKEN",
                "ACCESS_TOKEN",
                "BEARER_TOKEN",
                "TOKEN",
            ]
            if os.environ.get(name)
        ),
        None,
    )
    api_key = next(
        (
            os.environ.get(name)
            for name in ["NUCLIA_API_KEY", "API_KEY", "X_API_KEY"]
            if os.environ.get(name)
        ),
        None,
    )
    if api_key:
        headers["X-NUCLIA-SERVICEACCOUNT"] = f"Bearer {api_key}"
    elif token_bearer:
        headers["Authorization"] = f"Bearer {token_bearer}"
    return headers


async def ask_api(
    query_text: str,
    *,
    session: Optional[aiohttp.ClientSession] = None,
    chat_history: Optional[List[Dict[str, str]]] = None,
) -> Dict[str, Any]:
    url = os.environ.get("API_URL", DEFAULT_API_URL)
    payload: Dict[str, Any] = {
        "query": query_text,
        "filters": [],
        "prefer_markdown": True,
        "citations": True,
        "max_tokens": 0,
    }
    if chat_history:
        payload["chat_history"] = chat_history
    headers = _build_headers()

    owns_session = False
    resp_json: Dict[str, Any] = {}
    try:
        if session is None:
            timeout = ClientTimeout(total=float(os.environ.get("API_TIMEOUT", "30")))
            session = aiohttp.ClientSession(timeout=timeout)
            owns_session = True
        async with session.post(url, data=json.dumps(payload).encode("utf-8"), headers=headers) as resp:
            text = await resp.text()
            if resp.status >= 400:
                # Do not raise; return structured error for the caller to handle
                try:
                    body = json.loads(text)
                except Exception:
                    body = text
                return {
                    "error": {
                        "status": resp.status,
                        "message": _extract_error_message(body) or "HTTP error",
                        "body": body,
                    }
                }
            content_type = resp.headers.get("Content-Type", "")
            if "application/json" in content_type:
                resp_json = await resp.json(content_type=None)
            else:
                # Try to parse anyway
                try:
                    resp_json = json.loads(text)
                except Exception:
                    resp_json = {"raw": text}
    except asyncio.TimeoutError as e:
        return {"error": {"status": "timeout", "message": str(e)}}
    except ClientError as e:
        return {"error": {"status": "network", "message": str(e)}}
    finally:
        if owns_session and session is not None:
            await session.close()
    return resp_json


def extract_answer_text(response_json: Dict[str, Any]) -> Optional[str]:
    # We need the field answer.answer
    answer_obj = response_json.get("answer") if isinstance(response_json, dict) else None
    if isinstance(answer_obj, dict):
        inner_answer = answer_obj.get("answer")
        if isinstance(inner_answer, str) and inner_answer.strip():
            return inner_answer.strip()
    return None


def _extract_error_message(body: Any) -> Optional[str]:
    if isinstance(body, dict):
        for key in ("detail", "message", "error"):
            val = body.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip()
    if isinstance(body, str) and body.strip():
        return body.strip()[:300]
    return None


def extract_source_titles(response_json: Dict[str, Any], *, max_titles: Optional[int] = 5) -> List[str]:
    """Extract ordered list of source titles from API response.

    Prefers the order defined by best_matches; falls back to all resources.
    """
    titles: List[str] = []
    if not isinstance(response_json, dict):
        return titles
    answer_obj = response_json.get("answer")
    if not isinstance(answer_obj, dict):
        return titles
    find_result = answer_obj.get("find_result")
    if not isinstance(find_result, dict):
        return titles

    resources = find_result.get("resources") or {}
    if not isinstance(resources, dict):
        resources = {}

    id_to_title: Dict[str, str] = {}
    for resource_id, resource_obj in resources.items():
        if isinstance(resource_obj, dict):
            title = resource_obj.get("title")
            if isinstance(title, str) and title.strip():
                id_to_title[resource_id] = title.strip()

    # Prefer best_matches order
    best_matches = find_result.get("best_matches") or []
    if isinstance(best_matches, list):
        for match in best_matches:
            if not isinstance(match, str):
                continue
            resource_id = match.split("/")[0]
            title = id_to_title.get(resource_id)
            if title and title not in titles:
                titles.append(title)
                if max_titles and len(titles) >= max_titles:
                    return titles

    # Fallback: include remaining titles
    for _, title in id_to_title.items():
        if title and title not in titles:
            titles.append(title)
            if max_titles and len(titles) >= max_titles:
                break
    return titles


