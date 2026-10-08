"""Trigger matching engine, text normalization, anti-spam cooldown, and loop prevention."""

from __future__ import annotations

import logging
import re
import time
from collections import deque
from typing import Any, Deque, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)

# Characters to strip when removing punctuation, keeping alphanumeric and spaces
PUNCTUATION_PATTERN = re.compile(r"[^\w\s]", re.UNICODE)
SPACES_PATTERN = re.compile(r"\s+", re.UNICODE)


CARD_REGEX = re.compile(r"(\d{4}(?:[\s-]?\d{4}){3}|\d{16})")


def format_card_response(template: str, raw_card_setting: str) -> str:
    """Format template with single or multiple card numbers, wrapping ONLY card numbers in <code> tags so descriptions remain tap-free."""
    if not raw_card_setting:
        return template.replace("{CARD_NUMBER}", "")

    # Split by newlines (real or escaped)
    lines = [line.strip() for line in raw_card_setting.replace("\\n", "\n").splitlines() if line.strip()]

    if not lines:
        return template.replace("{CARD_NUMBER}", "")

    formatted_lines = []
    for line in lines:
        match = CARD_REGEX.search(line)
        if match:
            card_num = match.group(1)
            # Everything around the card number is a description
            desc = line.replace(card_num, "").strip(" -—:,")
            if desc:
                formatted_lines.append(f"<code>{card_num}</code> — {desc}")
            else:
                formatted_lines.append(f"<code>{card_num}</code>")
        else:
            # No card number found → treat as plain header/description text (bold)
            formatted_lines.append(f"<b>{line}</b>")

    cards_block = "\n".join(formatted_lines)

    if "<code>{CARD_NUMBER}</code>" in template:
        return template.replace("<code>{CARD_NUMBER}</code>", cards_block)
    return template.replace("{CARD_NUMBER}", cards_block)


def normalize_text(text: str, remove_punct: bool = True) -> str:
    """Normalize input text for reliable comparison.

    - Converts to lowercase
    - Strips leading and trailing whitespaces
    - Collapses multiple whitespace characters to a single space
    - Optionally strips punctuation (useful for natural phrases like 'скинь карту, пожалуйста!')
    """
    if not text:
        return ""

    lowered = text.lower().strip()
    if remove_punct:
        lowered = PUNCTUATION_PATTERN.sub(" ", lowered)

    return SPACES_PATTERN.sub(" ", lowered).strip()


class CooldownManager:
    """Manages per-chat, per-trigger anti-spam cooldowns with automatic garbage collection."""

    def __init__(self) -> None:
        # (chat_id, trigger_id) -> timestamp
        self._last_triggered: Dict[Tuple[int, int], float] = {}
        self._last_cleanup: float = time.time()

    def is_on_cooldown(self, chat_id: int, trigger_id: int, cooldown_seconds: int) -> bool:
        """Check if a trigger is currently in cooldown for the given chat."""
        if cooldown_seconds <= 0:
            return False

        self._maybe_cleanup(cooldown_seconds)
        key = (chat_id, trigger_id)
        now = time.time()
        last_time = self._last_triggered.get(key)

        if last_time is not None and (now - last_time) < cooldown_seconds:
            logger.debug(
                f"Cooldown active for chat_id={chat_id}, trigger_id={trigger_id}: "
                f"{cooldown_seconds - (now - last_time):.1f}s remaining"
            )
            return True
        return False

    def record_trigger(self, chat_id: int, trigger_id: int) -> None:
        """Record trigger activation timestamp."""
        self._last_triggered[(chat_id, trigger_id)] = time.time()

    def _maybe_cleanup(self, cooldown_seconds: int) -> None:
        """Periodically clean up expired entries to keep memory bounded."""
        now = time.time()
        if now - self._last_cleanup < 300:  # Run cleanup at most every 5 minutes
            return

        self._last_cleanup = now
        cutoff = now - max(cooldown_seconds * 2, 3600)
        keys_to_remove = [k for k, v in self._last_triggered.items() if v < cutoff]
        for k in keys_to_remove:
            del self._last_triggered[k]


class LoopGuard:
    """Tracks recently generated messages and hashes to prevent infinite auto-reply loops."""

    def __init__(self, max_history: int = 200) -> None:
        self._recent_message_ids: Set[int] = set()
        self._recent_order: Deque[int] = deque(maxlen=max_history)
        self._recent_response_hashes: Deque[int] = deque(maxlen=max_history)

    def record_bot_reply(self, message_id: Optional[int], response_text: str) -> None:
        """Record outgoing message metadata."""
        if message_id:
            self._recent_message_ids.add(message_id)
            self._recent_order.append(message_id)
            if len(self._recent_message_ids) > self._recent_order.maxlen:  # type: ignore[operator]
                # Prune oldest
                oldest = self._recent_order.popleft()
                self._recent_message_ids.discard(oldest)

        self._recent_response_hashes.append(hash(response_text.strip()))

    def is_loop(self, message_id: int, message_text: Optional[str] = None) -> bool:
        """Check if message originated from bot auto-replies."""
        if message_id in self._recent_message_ids:
            return True
        if message_text:
            text_hash = hash(message_text.strip())
            if text_hash in self._recent_response_hashes:
                return True
        return False


class TriggerMatcher:
    """Matches incoming messages against stored trigger rules."""

    @staticmethod
    def match(text: str, triggers: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        """Find the first matching trigger for the given message text.

        Evaluation order:
        1. Exact matches (highest priority)
        2. Contains matches (longest matching keyword first)
        """
        if not text:
            return None

        # Normalized versions for comparison:
        # 1. raw_norm: preserves command prefix like '.card' or '/card'
        raw_norm = normalize_text(text, remove_punct=False)
        # 2. nopunct_norm: removes commas, questions, dots for natural sentences
        nopunct_norm = normalize_text(text, remove_punct=True)

        exact_candidate: Optional[Dict[str, Any]] = None
        contains_candidates: List[Dict[str, Any]] = []

        for trig in triggers:
            stored_kw = trig.get("keyword", "")
            match_type = trig.get("match_type", "exact")

            # Stored keyword normalized both ways
            kw_raw = normalize_text(stored_kw, remove_punct=False)
            kw_nopunct = normalize_text(stored_kw, remove_punct=True)

            if match_type == "exact":
                # Check exact equality against raw or nopunct
                if raw_norm == kw_raw or (kw_nopunct and nopunct_norm == kw_nopunct):
                    exact_candidate = trig
                    break
            elif match_type == "contains":
                # Check containment: either with symbols or purely word boundaries
                target_kw = kw_nopunct if kw_nopunct else kw_raw
                search_text = nopunct_norm if kw_nopunct else raw_norm

                if target_kw and target_kw in search_text:
                    contains_candidates.append(trig)

        if exact_candidate:
            return exact_candidate

        if contains_candidates:
            # Sort by longest keyword to match most specific trigger first
            contains_candidates.sort(key=lambda x: len(x.get("keyword", "")), reverse=True)
            return contains_candidates[0]

        return None
