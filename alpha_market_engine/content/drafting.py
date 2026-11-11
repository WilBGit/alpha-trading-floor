from __future__ import annotations

import hashlib
import json
import re
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from typing import Any, Protocol

from alpha_market_engine.content.opportunities import ContentDecision, ContentOpportunity

PROMPT_VERSION = "phase5b-v1"
VOICE_PROFILE = "ALPHA_TRADING_FLOOR_TRADER_V1"

PROHIBITED_INFERENCES = (
    "institutional buying", "institutional selling", "smart money", "sector rotation", "capital rotation",
    "market manipulation", "short squeeze", "gamma squeeze", "dealer positioning", "options flow",
    "dark pool", "algorithmic manipulation", "news catalyst", "earnings", "macro catalyst",
)
CERTAINTY_PHRASES = ("guaranteed", "can't lose", "cannot lose", "easy money", "will go higher", "will dump", "next leg is up", "next leg is higher", "will keep running", "going to squeeze")
FINANCIAL_INSTRUCTIONS = ("buy ", "sell ", "short ", "put 50%", "all in", "load up", "risk your account")
GENERIC_AI_PHRASES = ("according to the data", "it is important to note", "market participants", "investors should consider", "this indicates that", "in conclusion", "overall, the market", "it remains to be seen", "as we continue to monitor", "today's market activity")


@dataclass(frozen=True, slots=True)
class NumericFact:
    fact_id: str
    semantic_name: str
    raw_decimal_value: Decimal
    approved_display_value: str
    unit: str
    comparison_context: str = ""
    source_analysis_id: str = ""
    source_opportunity_id: str = ""


@dataclass(frozen=True, slots=True)
class ContentDraftPackage:
    draft_request_id: str
    analysis_id: str
    opportunity_id: str
    opportunity_key: str
    opportunity_type: str
    symbols: tuple[str, ...]
    primary_symbol: str | None
    session_date: str
    detected_at: datetime
    source_regime: str
    source_regime_confidence: int
    importance_score: int
    confidence_score: int
    freshness_score: int
    novelty_score: int
    content_score: Decimal
    confirmation_state: str
    facts: dict[str, Any]
    allowed_numeric_facts: tuple[NumericFact, ...]
    allowed_symbol_facts: tuple[str, ...]
    allowed_claims: tuple[str, ...]
    prohibited_claims: tuple[str, ...]
    source_event_ids: tuple[str, ...]
    source_observation_keys: tuple[str, ...]
    source_divergence_types: tuple[str, ...]
    interpretation_boundary: str
    prohibited_inferences: tuple[str, ...]
    content_pattern_key: str
    platform: str = "X"
    voice_profile: str = VOICE_PROFILE
    max_length: int = 280
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def as_prompt_context(self) -> dict[str, Any]:
        return {
            "opportunity_type": self.opportunity_type,
            "symbols": list(self.allowed_symbol_facts),
            "allowed_claims": list(self.allowed_claims),
            "allowed_numeric_facts": [
                {"fact_id": f.fact_id, "value": f.approved_display_value, "unit": f.unit, "context": f.comparison_context}
                for f in self.allowed_numeric_facts
            ],
            "interpretation_boundary": self.interpretation_boundary,
            "prohibited_claims": list(self.prohibited_claims),
            "platform": self.platform,
            "voice_profile": self.voice_profile,
            "max_length": self.max_length,
        }


@dataclass(frozen=True, slots=True)
class DraftProviderResult:
    content_text: str
    provider: str
    model: str
    request_id: str
    temperature: str
    max_output_tokens: int
    latency_ms: int
    raw_response_available: bool = False


@dataclass(frozen=True, slots=True)
class GeneratedContentDraft:
    draft_id: str
    draft_request_id: str
    analysis_id: str
    opportunity_id: str
    opportunity_key: str
    platform: str
    content_text: str
    content_hash: str
    provider: str
    model: str
    prompt_version: str
    voice_profile: str
    draft_attempt: int
    generated_at: datetime
    character_count: int
    validation_status: str
    validation_failures: tuple[str, ...]
    factual_claims: tuple[str, ...]
    numeric_claims: tuple[str, ...]
    symbol_claims: tuple[str, ...]
    quality_score: int
    final_decision: str
    final_decision_reasons: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class DraftValidationResult:
    is_valid: bool
    validation_status: str
    failures: tuple[str, ...]
    warnings: tuple[str, ...]
    extracted_claims: tuple[str, ...]
    numeric_claims: tuple[str, ...]
    symbol_claims: tuple[str, ...]
    unsupported_claims: tuple[str, ...]
    financial_safety_failures: tuple[str, ...]
    character_count: int
    exact_duplicate: bool
    near_duplicate: bool
    similarity_score: Decimal
    similar_draft_id: str | None
    quality_score: int


@dataclass(frozen=True, slots=True)
class ContentDraftResult:
    opportunity_id: str
    draft_request_id: str
    attempts: tuple[GeneratedContentDraft, ...]
    approved_draft: GeneratedContentDraft | None
    final_decision: str
    final_decision_reasons: tuple[str, ...]
    warnings: tuple[str, ...] = ()


class ContentDraftingProvider(Protocol):
    def draft(self, package: ContentDraftPackage, rewrite_reasons: tuple[str, ...] = ()) -> DraftProviderResult: ...


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().replace("\r\n", "\n")).casefold()


def content_hash(text: str) -> str:
    return hashlib.sha256(_normalize(text).encode("utf-8")).hexdigest()


def _tokens(text: str) -> set[str]:
    return {token for token in re.findall(r"[a-z0-9$%]+", _normalize(text)) if len(token) > 2}


def _jaccard(left: str, right: str) -> Decimal:
    a, b = _tokens(left), _tokens(right)
    if not a and not b:
        return Decimal("1")
    return Decimal(len(a & b)) / Decimal(max(1, len(a | b)))


def build_draft_package(opportunity: ContentOpportunity, *, platform: str = "X", max_length: int = 280) -> ContentDraftPackage:
    facts = dict(opportunity.facts)
    numeric: list[NumericFact] = []
    for key, semantic, unit, display_key in (
        ("index_a_change_percent", "index A change", "percent", "index_a_change_percent"),
        ("index_b_change_percent", "index B change", "percent", "index_b_change_percent"),
        ("relative_difference", "relative-performance difference", "percentage_points", "display_difference"),
    ):
        if facts.get(key) is None:
            continue
        try:
            raw = Decimal(str(facts[key]))
        except (InvalidOperation, ValueError):
            continue
        display = str(facts.get(display_key, facts[key]))
        if unit == "percent":
            display = format(raw.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP), "f")
        numeric.append(NumericFact(f"{key}", semantic, raw, display, unit, f"{facts.get('index_a', '')} vs {facts.get('index_b', '')}", opportunity.analysis_id, opportunity.opportunity_id))
    claims: list[str] = []
    if opportunity.opportunity_type.value == "QQQ_LEADERSHIP":
        claims.append("QQQ has deterministic relative strength versus SPY")
    elif "VWAP" in opportunity.opportunity_type.value:
        claims.append(f"{opportunity.primary_symbol or 'The symbol'} has the certified VWAP relationship")
    elif opportunity.opportunity_type.value.startswith("OR"):
        claims.append(f"{opportunity.primary_symbol or 'The symbol'} has a certified opening-range event")
    elif opportunity.opportunity_type.value == "MARKET_ROTATION":
        claims.append("The certified regime is ROTATION")
    else:
        claims.append(f"The certified opportunity is {opportunity.opportunity_type.value}")
    return ContentDraftPackage(
        draft_request_id=str(uuid.uuid4()), analysis_id=opportunity.analysis_id, opportunity_id=opportunity.opportunity_id,
        opportunity_key=opportunity.opportunity_key, opportunity_type=opportunity.opportunity_type.value,
        symbols=opportunity.symbols, primary_symbol=opportunity.primary_symbol, session_date=opportunity.session_date,
        detected_at=opportunity.detected_at, source_regime=opportunity.source_regime, source_regime_confidence=opportunity.source_regime_confidence,
        importance_score=opportunity.importance_score, confidence_score=opportunity.confidence_score, freshness_score=opportunity.freshness_score,
        novelty_score=opportunity.novelty_score, content_score=opportunity.content_score, confirmation_state=opportunity.confirmation_state.value,
        facts=facts, allowed_numeric_facts=tuple(numeric), allowed_symbol_facts=tuple(sorted(opportunity.symbols)), allowed_claims=tuple(claims),
        prohibited_claims=PROHIBITED_INFERENCES, source_event_ids=opportunity.source_event_ids, source_observation_keys=opportunity.source_observation_keys,
        source_divergence_types=opportunity.source_divergence_types, interpretation_boundary=opportunity.interpretation_boundary,
        prohibited_inferences=PROHIBITED_INFERENCES, content_pattern_key=opportunity.content_pattern_key, platform=platform, max_length=max_length,
    )


def build_prompt(package: ContentDraftPackage, rewrite_reasons: tuple[str, ...] = ()) -> str:
    return """Write exactly one Alpha Trading Floor X post. Return only the post text.\n\nVOICE RULES: direct, calm, concise, price-action focused, human trader voice.\nALLOWED FACT PACKAGE:\n""" + json.dumps(package.as_prompt_context(), sort_keys=True) + "\nINTERPRETATION BOUNDARY: " + package.interpretation_boundary + "\nPROHIBITED: use no unsupported causes, catalysts, tickers, numbers, institutional claims, guarantees, or direct personalized financial instructions.\nPLATFORM: maximum 280 characters, use $ tickers, no hashtags, no links, no engagement bait.\n" + ("REWRITE THESE VALIDATION FAILURES: " + "; ".join(rewrite_reasons) if rewrite_reasons else "")


class FakeDraftingProvider:
    def __init__(self, responses: list[str] | None = None) -> None:
        self.responses = list(responses or [])
        self.calls = 0

    def queue(self, response: str) -> None:
        self.responses.append(response)

    def draft(self, package: ContentDraftPackage, rewrite_reasons: tuple[str, ...] = ()) -> DraftProviderResult:
        self.calls += 1
        text = self.responses.pop(0) if self.responses else self._default(package)
        return DraftProviderResult(text, "fake", "fake-deterministic-v1", f"fake-{self.calls}", "0", 100, 0, False)

    def _default(self, package: ContentDraftPackage) -> str:
        if package.opportunity_type == "QQQ_LEADERSHIP":
            point = next((f.approved_display_value for f in package.allowed_numeric_facts if f.unit == "percentage_points"), "")
            return f"$QQQ putting {point} percentage points of distance on $SPY. Relative strength is showing up."
        return f"${package.primary_symbol or package.symbols[0]} has a certified {package.opportunity_type.replace('_', ' ').lower()} here. Follow-through still needs to prove itself."


class CodexDraftingProvider:
    def __init__(self, *, model: str = "gpt-5.6-sol") -> None:
        self.model = model

    def _token(self) -> str:
        auth = json.loads(Path.home().joinpath(".hermes/auth.json").read_text())
        pools = auth.get("credential_pool", {}).get("openai-codex", [])
        if not pools:
            raise RuntimeError("Codex OAuth credentials unavailable")
        return pools[0]["access_token"]

    def draft(self, package: ContentDraftPackage, rewrite_reasons: tuple[str, ...] = ()) -> DraftProviderResult:
        import httpx
        started = time.monotonic()
        response = httpx.post("https://chatgpt.com/backend-api/codex/responses", headers={"Authorization": f"Bearer {self._token()}", "Content-Type": "application/json"}, json={"model": self.model, "store": False, "stream": True, "input": [{"role": "user", "content": build_prompt(package, rewrite_reasons)}]}, timeout=90)
        response.raise_for_status()
        text_parts: list[str] = []
        for line in response.text.splitlines():
            if not line.startswith("data:"):
                continue
            try:
                item = json.loads(line[5:].strip())
            except json.JSONDecodeError:
                continue
            if item.get("type") == "response.output_text.delta":
                text_parts.append(item.get("delta", ""))
        text = "".join(text_parts).strip()
        if not text:
            raise RuntimeError("drafting provider returned no text")
        return DraftProviderResult(text, "openai-codex", self.model, str(uuid.uuid4()), "unspecified", 256, int((time.monotonic() - started) * 1000), True)


class DraftValidator:
    def __init__(self, *, recent_drafts: list[Any] | None = None) -> None:
        self.recent_drafts = recent_drafts or []

    def validate(self, text: str, package: ContentDraftPackage) -> DraftValidationResult:
        failures: list[str] = []
        warnings: list[str] = []
        normalized = _normalize(text)
        explicit_symbols = [token.upper().lstrip("$") for token in re.findall(r"\$[A-Z]{2,5}\b", text)]
        bare_tokens = [token.upper() for token in re.findall(r"\b[A-Z]{2,5}\b", text)]
        bare_stopwords = {"IS", "IT", "ON", "TO", "UP", "BY", "THE", "AND", "NOW", "FOR", "OR", "HAS", "OUT", "INTO", "VWAP"}
        bare_symbols = [token for token in bare_tokens if token in package.allowed_symbol_facts]
        unexpected_bare = [token for token in bare_tokens if token not in package.allowed_symbol_facts and token not in bare_stopwords]
        symbols = explicit_symbols + bare_symbols + unexpected_bare
        symbols = sorted(set(symbols))
        unexpected_symbols = [symbol for symbol in symbols if symbol not in package.allowed_symbol_facts]
        if unexpected_symbols:
            failures.append("UNAPPROVED_SYMBOL:" + ",".join(unexpected_symbols))
        numeric_claims = re.findall(r"\b\d+(?:\.\d+)?\s*(?:%|percentage\s+points?|points?)", text, flags=re.IGNORECASE)
        allowed_values = {(f.approved_display_value, f.unit) for f in package.allowed_numeric_facts}
        for claim in numeric_claims:
            match = re.search(r"(\d+(?:\.\d+)?)\s*(%|percentage\s+points?|points?)", claim, re.IGNORECASE)
            if not match:
                continue
            value, unit = match.group(1), match.group(2).lower()
            semantic_unit = "percent" if unit == "%" else "percentage_points"
            if (value, semantic_unit) not in allowed_values:
                failures.append("UNAPPROVED_NUMERIC_CLAIM:" + claim)
        for phrase in PROHIBITED_INFERENCES:
            if phrase in normalized:
                failures.append("UNSUPPORTED_INFERENCE:" + phrase)
        for phrase in CERTAINTY_PHRASES:
            if phrase in normalized:
                failures.append("UNSUPPORTED_CERTAINTY:" + phrase)
        for phrase in FINANCIAL_INSTRUCTIONS:
            if normalized.startswith(phrase) or f" {phrase}" in normalized:
                failures.append("UNSAFE_FINANCIAL_INSTRUCTION:" + phrase.strip())
        if package.facts.get("quality") == "DEGRADED" and any(phrase in normalized for phrase in ("clean", "confirmed by every feed", "high-confidence")):
            failures.append("DEGRADED_FACT_MISREPRESENTED")
        if len(text) > package.max_length:
            failures.append("PLATFORM_LENGTH_EXCEEDED")
        relative_text = normalized.replace("relative-strength", "relative strength")
        if package.opportunity_type == "QQQ_LEADERSHIP" and not ("qqq" in normalized and "spy" in normalized and any(word in relative_text for word in ("lead", "outperform", "relative strength", "distance"))):
            failures.append("MISSING_RELATIVE_PERFORMANCE_CLAIM")
        if package.opportunity_type == "MARKET_ROTATION" and re.search(r"(?:money|capital|funds).{0,30}rotat", normalized):
            failures.append("UNSUPPORTED_INFERENCE:capital rotation")
        if package.opportunity_type.startswith("OR"):
            expected = re.search(r"OR(5|15|30)", package.opportunity_type)
            if expected and not re.search(r"OR" + expected.group(1), text, re.IGNORECASE):
                failures.append("WRONG_OPENING_RANGE")
        vwap_position = str(package.facts.get("vwap_position", "")).casefold()
        if vwap_position == "below" and any(phrase in normalized for phrase in ("above vwap", "holding above vwap")):
            failures.append("WRONG_VWAP_POSITION")
        if "VWAP_RECLAIM" in package.opportunity_type and not any(term in normalized for term in ("reclaim", "back above", "above vwap")):
            failures.append("MISSING_VWAP_RECLAIM_CLAIM")
        if any(phrase in normalized for phrase in GENERIC_AI_PHRASES):
            warnings.append("GENERIC_AI_LANGUAGE")
        exact = False
        near = False
        similarity = Decimal("0")
        similar_id = None
        current_hash = content_hash(text)
        for draft in self.recent_drafts:
            old_text = getattr(draft, "content_text", None) or getattr(draft, "content", "")
            if content_hash(old_text) == current_hash:
                exact, similarity, similar_id = True, Decimal("1"), str(getattr(draft, "draft_id", getattr(draft, "id", "")))
                break
            candidate = _jaccard(text, old_text)
            if candidate > similarity:
                similarity, similar_id = candidate, str(getattr(draft, "draft_id", getattr(draft, "id", "")))
        near = similarity >= Decimal("0.75")
        if exact:
            failures.append("EXACT_DUPLICATE")
        elif near:
            failures.append("NEAR_DUPLICATE")
        quality = self.quality_score(text, package, warnings)
        if "GENERIC_AI_LANGUAGE" in warnings:
            quality = max(0, quality - 20)
        valid = not failures and quality >= 70
        status = "VALID" if valid else "INVALID"
        if not valid and quality >= 70 and all(item in {"PLATFORM_LENGTH_EXCEEDED", "NEAR_DUPLICATE", "GENERIC_AI_LANGUAGE"} for item in failures + warnings):
            status = "REWRITE"
        return DraftValidationResult(valid, status, tuple(failures), tuple(warnings), tuple(f"{package.opportunity_type}:{claim}" for claim in package.allowed_claims), tuple(numeric_claims), tuple(symbols), tuple(failures), tuple(item for item in failures if item.startswith("UNSAFE_")), len(text), exact, near, similarity, similar_id, quality)

    def quality_score(self, text: str, package: ContentDraftPackage, warnings: list[str] | None = None) -> int:
        warnings = warnings or []
        score = 100
        if len(text) < 60:
            score -= 10
        if len(text) > 240:
            score -= 5
        if text.count("!") > 1:
            score -= 8
        if text.count("#"):
            score -= 5
        if "GENERIC_AI_LANGUAGE" in warnings:
            score -= 10
        return max(0, min(100, score))


class ContentDraftingService:
    def __init__(self, provider: ContentDraftingProvider, *, repository: Any | None = None, max_attempts: int = 3, safety: Any | None = None) -> None:
        self.provider = provider
        self.repository = repository
        self.max_attempts = max_attempts
        self.safety = safety

    def draft(self, opportunity: ContentOpportunity) -> ContentDraftResult:
        package = build_draft_package(opportunity)
        recent = self.repository.get_recent_generated_drafts(100) if self.repository is not None else []
        validator = DraftValidator(recent_drafts=recent)
        attempts: list[GeneratedContentDraft] = []
        reasons: tuple[str, ...] = ()
        final_decision = "REJECT"
        final_reasons: tuple[str, ...] = ()
        approved: GeneratedContentDraft | None = None
        for number in range(1, self.max_attempts + 1):
            try:
                provider_result = self.provider.draft(package, reasons)
            except Exception as exc:
                if self.safety is not None:
                    self.safety.record_drafting_failure(type(exc).__name__)
                raise
            validation = validator.validate(provider_result.content_text, package)
            decision = "APPROVE" if validation.is_valid else "REWRITE" if number < self.max_attempts and validation.validation_status == "REWRITE" else "REJECT"
            draft = GeneratedContentDraft(str(uuid.uuid4()), package.draft_request_id, package.analysis_id, package.opportunity_id, package.opportunity_key, package.platform, provider_result.content_text, content_hash(provider_result.content_text), provider_result.provider, provider_result.model, PROMPT_VERSION, package.voice_profile, number, datetime.now(UTC), validation.character_count, validation.validation_status, validation.failures, validation.extracted_claims, validation.numeric_claims, validation.symbol_claims, validation.quality_score, decision, validation.failures or validation.warnings)
            attempts.append(draft)
            if self.repository is not None:
                self.repository.create_generated_content_draft(draft)
            if decision == "APPROVE":
                approved, final_decision, final_reasons = draft, decision, ()
                break
            reasons = tuple(validation.failures) + tuple(validation.warnings)
            final_decision, final_reasons = decision, reasons
        return ContentDraftResult(package.opportunity_id, package.draft_request_id, tuple(attempts), approved, final_decision, final_reasons)
