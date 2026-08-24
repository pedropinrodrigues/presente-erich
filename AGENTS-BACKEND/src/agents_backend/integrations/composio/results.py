from __future__ import annotations

import html
import json
import re
from typing import Any

_TAG = re.compile(r"<[^>]+>")
_SPACE = re.compile(r"\s+")


def _plain_excerpt(value: Any, maximum: int) -> str:
    text = html.unescape(str(value or ""))
    text = _TAG.sub(" ", text)
    text = _SPACE.sub(" ", text).strip()
    if len(text) <= maximum:
        return text
    return text[: maximum - 1].rstrip() + "…"


def _decoded_mcp_content(payload: dict[str, Any]) -> Any:
    decoded: list[Any] = []
    for item in payload.get("content", []):
        if not isinstance(item, dict) or item.get("type") != "text":
            continue
        value = str(item.get("text", ""))
        try:
            decoded.append(json.loads(value))
        except json.JSONDecodeError:
            decoded.append(value)
    if len(decoded) == 1:
        return decoded[0]
    return decoded


def _compact_gmail_message(message: dict[str, Any], *, excerpt_limit: int) -> dict[str, Any]:
    preview = message.get("preview")
    preview_body = preview.get("body") if isinstance(preview, dict) else preview
    return {
        "message_id": str(message.get("messageId") or message.get("id") or ""),
        "thread_id": str(message.get("threadId") or ""),
        "sender": _plain_excerpt(message.get("sender"), 500),
        "recipient": _plain_excerpt(message.get("to"), 500),
        "subject": _plain_excerpt(
            message.get("subject") or (preview.get("subject") if isinstance(preview, dict) else ""),
            1000,
        ),
        "received_at": str(message.get("messageTimestamp") or message.get("date") or ""),
        "preview": _plain_excerpt(preview_body, 500),
        "text_excerpt": _plain_excerpt(message.get("messageText"), excerpt_limit),
        "labels": [str(item) for item in message.get("labelIds", [])[:20]],
    }


def _gmail_result(remote_slug: str, decoded: Any) -> dict[str, Any]:
    root = decoded if isinstance(decoded, dict) else {}
    data = root.get("data") if isinstance(root.get("data"), dict) else root
    raw_messages = data.get("messages") if isinstance(data, dict) else None
    if isinstance(raw_messages, list):
        messages = [
            _compact_gmail_message(message, excerpt_limit=1200)
            for message in raw_messages
            if isinstance(message, dict)
        ]
    elif isinstance(data, dict):
        messages = [_compact_gmail_message(data, excerpt_limit=8000)]
    else:
        messages = []
    return {
        "provider": "gmail",
        "operation": remote_slug,
        "content_scope": "headers, preview and plain-text excerpt; raw HTML omitted",
        "count_returned": len(messages),
        "result_size_estimate": data.get("resultSizeEstimate") if isinstance(data, dict) else None,
        "has_more": bool(data.get("nextPageToken")) if isinstance(data, dict) else False,
        "messages": messages,
        "successful": root.get("successful", True),
        "error": root.get("error"),
    }


def _calendar_time(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    return {
        key: str(value[key])
        for key in ("dateTime", "date", "timeZone")
        if value.get(key) is not None
    }


def _compact_calendar_event(event: dict[str, Any]) -> dict[str, Any]:
    attendees = event.get("attendees")
    attendee_values = attendees if isinstance(attendees, list) else []
    return {
        "event_id": str(event.get("id") or ""),
        "title": _plain_excerpt(event.get("summary") or "Sem título", 1000),
        "start": _calendar_time(event.get("start")),
        "end": _calendar_time(event.get("end")),
        "status": str(event.get("status") or ""),
        "location": _plain_excerpt(event.get("location"), 1000),
        "description_excerpt": _plain_excerpt(event.get("description"), 1500),
        "organizer": _plain_excerpt(
            event.get("organizer", {}).get("email")
            if isinstance(event.get("organizer"), dict)
            else "",
            500,
        ),
        "attendees": [
            {
                "email": _plain_excerpt(attendee.get("email"), 500),
                "response_status": str(attendee.get("responseStatus") or ""),
            }
            for attendee in attendee_values[:30]
            if isinstance(attendee, dict)
        ],
        "calendar": _plain_excerpt(
            event.get("calendar") or event.get("calendarSummary") or event.get("calendar_id"),
            500,
        ),
        "link": str(event.get("htmlLink") or event.get("display_url") or ""),
    }


def _calendar_result(remote_slug: str, decoded: Any) -> dict[str, Any]:
    root = decoded if isinstance(decoded, dict) else {}
    data = root.get("data") if isinstance(root.get("data"), dict) else root
    raw_summary = data.get("summary_view") if isinstance(data, dict) else None
    raw_events = data.get("items") if isinstance(data, dict) else None
    if not isinstance(raw_events, list) and isinstance(data, dict):
        raw_events = data.get("events")
    if isinstance(raw_events, list) and raw_events:
        events = [_compact_calendar_event(event) for event in raw_events if isinstance(event, dict)]
    elif isinstance(raw_summary, list):
        events = [
            {
                "event_id": str(event.get("event_id") or ""),
                "title": _plain_excerpt(event.get("title") or "Sem título", 1000),
                "start": str(event.get("start") or ""),
                "end": str(event.get("end") or ""),
                "is_all_day": bool(event.get("is_all_day", False)),
                "calendar": _plain_excerpt(event.get("calendar"), 500),
                "link": str(event.get("display_url") or ""),
            }
            for event in raw_summary
            if isinstance(event, dict)
        ]
    else:
        events = []
    calendars = data.get("calendars_queried") if isinstance(data, dict) else None
    return {
        "provider": "googlecalendar",
        "operation": remote_slug,
        "calendar_timezone": data.get("timeZone") if isinstance(data, dict) else None,
        "calendars_queried": [
            {
                "id": str(calendar.get("id") or ""),
                "name": _plain_excerpt(calendar.get("summary"), 500),
            }
            for calendar in calendars
            if isinstance(calendar, dict)
        ]
        if isinstance(calendars, list)
        else [],
        "count_returned": len(events),
        "events": events,
        "errors_by_calendar": data.get("errors_by_calendar") if isinstance(data, dict) else None,
        "successful": root.get("successful", True),
        "error": root.get("error"),
    }


def _compact_generic(
    value: Any,
    *,
    depth: int = 0,
    string_limit: int = 4000,
    list_limit: int = 50,
) -> Any:
    if depth >= 8:
        return None
    if isinstance(value, dict):
        return {
            str(key): _compact_generic(
                item,
                depth=depth + 1,
                string_limit=string_limit,
                list_limit=list_limit,
            )
            for key, item in list(value.items())[:100]
            if str(key).casefold() not in {"raw", "raw_html", "html", "payload"}
        }
    if isinstance(value, list):
        return [
            _compact_generic(
                item,
                depth=depth + 1,
                string_limit=string_limit,
                list_limit=list_limit,
            )
            for item in value[:list_limit]
        ]
    if isinstance(value, str):
        return _plain_excerpt(value, string_limit)
    return value


def normalize_mcp_result(remote_slug: str, payload: dict[str, Any]) -> dict[str, Any]:
    decoded = _decoded_mcp_content(payload)
    if remote_slug in {"GMAIL_FETCH_EMAILS", "GMAIL_FETCH_MESSAGE_BY_MESSAGE_ID"}:
        return _gmail_result(remote_slug, decoded)
    if remote_slug in {
        "GOOGLECALENDAR_EVENTS_LIST",
        "GOOGLECALENDAR_EVENTS_LIST_ALL_CALENDARS",
    }:
        return _calendar_result(remote_slug, decoded)
    return {
        "operation": remote_slug,
        "result": _compact_generic(decoded),
        "is_error": bool(payload.get("isError", False)),
    }


def bound_normalized_result(
    value: dict[str, Any], maximum_characters: int = 60_000
) -> dict[str, Any]:
    encoded = json.dumps(value, ensure_ascii=False)
    if len(encoded) <= maximum_characters:
        return value
    for string_limit, list_limit in ((2000, 30), (1000, 20), (500, 10)):
        compacted = _compact_generic(
            value,
            string_limit=string_limit,
            list_limit=list_limit,
        )
        encoded = json.dumps(compacted, ensure_ascii=False)
        if isinstance(compacted, dict) and len(encoded) <= maximum_characters:
            compacted["partial_result"] = True
            compacted["partial_reason"] = "normalized result exceeded the agent context limit"
            return compacted
    return {
        "partial_result": True,
        "partial_reason": "normalized result exceeded the agent context limit",
        "available_fields": list(value)[:50],
    }
