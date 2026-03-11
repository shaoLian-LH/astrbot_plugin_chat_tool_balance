from __future__ import annotations

from collections.abc import Callable

from pipeline.contracts import ImageFacts, NormalizedEvent, ToolIntentDecision

ToolIntentClassifier = Callable[[str, str, NormalizedEvent], tuple[float, str]] | Callable[
    [str, str], tuple[float, str]
]


class ToolIntentStage:
    """Tool intent classification with threshold gating."""

    def __init__(
        self,
        tool_intent_model: str,
        chat_default_model: str,
        threshold: float = 0.65,
        classifier: ToolIntentClassifier | None = None,
    ) -> None:
        self.model_name = (tool_intent_model or "").strip() or (chat_default_model or "").strip()
        self.threshold = self._clamp(threshold)
        self.classifier = classifier
        self.minimal_prompt = "请先尝试调用可用工具，失败后再回退普通聊天回答。"

    def process(
        self,
        event: NormalizedEvent,
        image_facts: tuple[ImageFacts, ...] = (),
    ) -> ToolIntentDecision:
        payload = event.intent_payload(image_facts)
        confidence, reason_code = self._predict(payload, event)
        route = "tool" if confidence >= self.threshold else "chat"
        prompt_injection = self.minimal_prompt if route == "tool" else ""
        return ToolIntentDecision(
            route=route,
            confidence=confidence,
            reason_code=reason_code,
            model_name=self.model_name,
            prompt_injection=prompt_injection,
        )

    def _predict(self, payload: str, event: NormalizedEvent) -> tuple[float, str]:
        if self.classifier is not None:
            try:
                score, reason = self.classifier(payload, self.model_name, event)
                return self._clamp(score), reason or "classifier_result"
            except TypeError:
                try:
                    legacy_classifier = self.classifier
                    score, reason = legacy_classifier(payload, self.model_name)  # type: ignore[misc]
                except Exception:
                    score, _ = self._heuristic_predict(payload)
                    return score, "model_error_fallback"
                return self._clamp(score), reason or "classifier_result"
            except Exception:
                score, _ = self._heuristic_predict(payload)
                return score, "model_error_fallback"
        return self._heuristic_predict(payload)

    def _heuristic_predict(self, payload: str) -> tuple[float, str]:
        lowered = payload.lower()
        intent_keywords = (
            "查询",
            "查一下",
            "搜索",
            "计算",
            "天气",
            "日历",
            "提醒",
            "执行",
            "run",
            "search",
            "tool",
        )
        if any(keyword in lowered for keyword in intent_keywords):
            return 0.92, "keyword_hit"
        if "?" in payload or "？" in payload:
            return 0.45, "question_like"
        return 0.22, "chat_like"

    @staticmethod
    def _clamp(value: float) -> float:
        return max(0.0, min(1.0, float(value)))
