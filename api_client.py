#!/usr/bin/env python3
import os
import json
from typing import Any, Dict, Optional

import aiohttp
from aiohttp import ClientTimeout


DEFAULT_API_URL = "https://lbf7-hackaton.replit.app/ask"


def _build_headers() -> Dict[str, str]:
    headers: Dict[str, str] = {
        "User-Agent": "python-aiohttp",
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


async def ask_api(query_text: str, *, session: Optional[aiohttp.ClientSession] = None) -> Dict[str, Any]:
    url = os.environ.get("API_URL", DEFAULT_API_URL)
    payload = {
        "query": query_text,
        "filters": [],
        "prefer_markdown": True,
        "citations": True,
        "max_tokens": 0,
    }
    headers = _build_headers()

    owns_session = False
    resp_json: Dict[str, Any] = {}
    try:
        if session is None:
            timeout = ClientTimeout(total=float(os.environ.get("API_TIMEOUT", "30")))
            session = aiohttp.ClientSession(timeout=timeout)
            owns_session = True
        async with session.post(url, data=json.dumps(payload).encode("utf-8"), headers=headers) as resp:
            # Raise for non-2xx
            if resp.status >= 400:
                text = await resp.text()
                raise aiohttp.ClientResponseError(
                    request_info=resp.request_info,
                    history=resp.history,
                    status=resp.status,
                    message=text,
                    headers=resp.headers,
                )
            content_type = resp.headers.get("Content-Type", "")
            if "application/json" in content_type:
                resp_json = await resp.json(content_type=None)
            else:
                # Try to parse anyway
                text = await resp.text()
                try:
                    resp_json = json.loads(text)
                except Exception:
                    resp_json = {"raw": text}
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


