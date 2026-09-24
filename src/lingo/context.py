"""Bounded conversation context for live calls."""

from pipecat.processors.aggregators.llm_context import LLMContext, LLMContextMessage


class BoundedLLMContext(LLMContext):
    def __init__(self, *, max_messages: int = 20):
        super().__init__()
        self._max_messages = max_messages

    def add_message(self, message: LLMContextMessage):
        super().add_message(message)
        self._trim()

    def add_messages(self, messages: list[LLMContextMessage]):
        super().add_messages(messages)
        self._trim()

    def _trim(self) -> None:
        if len(self.messages) > self._max_messages:
            self.set_messages(self.messages[-self._max_messages :])
