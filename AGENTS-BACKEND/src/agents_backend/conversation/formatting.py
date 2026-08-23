from __future__ import annotations

import re

from .providers import TELEGRAM_PROVIDER

_MARKDOWN_LINK = re.compile(r"\[([^\]]+)\]\((https?://[^)]+)\)")
_BOLD = re.compile(r"\*\*(.+?)\*\*|__(.+?)__")
_ITALIC_STAR = re.compile(r"(?<!\w)\*([^*\n]+)\*(?!\w)")
_ITALIC_UNDERSCORE = re.compile(r"(?<!\w)_([^_\n]+)_(?!\w)")
_STRIKE = re.compile(r"~~(.+?)~~")
_HEADING = re.compile(r"^\s{0,3}#{1,6}\s+")
_BULLET = re.compile(r"^\s*[-*+]\s+")
_QUOTE = re.compile(r"^\s*>\s?")
_HORIZONTAL_RULE = re.compile(r"^\s*(?:[-*_]\s*){3,}$")


def telegram_plain_text(text: str) -> str:
    """Render common Markdown as readable Telegram text without parse_mode."""
    output: list[str] = []
    in_code_block = False
    for raw_line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = raw_line
        if line.strip().startswith("```"):
            in_code_block = not in_code_block
            continue
        if not in_code_block and _HORIZONTAL_RULE.fullmatch(line):
            continue
        if not in_code_block:
            line = _HEADING.sub("", line)
            line = _BULLET.sub("• ", line)
            line = _QUOTE.sub("", line)
            line = _MARKDOWN_LINK.sub(r"\1: \2", line)
            line = _BOLD.sub(lambda match: match.group(1) or match.group(2) or "", line)
            line = _ITALIC_STAR.sub(r"\1", line)
            line = _ITALIC_UNDERSCORE.sub(r"\1", line)
            line = _STRIKE.sub(r"\1", line)
            line = line.replace("`", "")
        output.append(line.rstrip())

    normalized = "\n".join(output).strip()
    return re.sub(r"\n{3,}", "\n\n", normalized)


def format_channel_text(provider: str, text: str) -> str:
    if provider == TELEGRAM_PROVIDER:
        return telegram_plain_text(text)
    return text.strip()
