from ai_platform.common.schemas import ChatRequest, ChatResponse


class FakeRuntimeClient:
    """Stands in for a RuntimeClient — RuntimeEngine or otherwise. Returns
    scripted responses keyed by call order (like FakeModelProvider in
    tests/runtime/conftest.py), or raises a scripted error for a given
    call, so EvalRunner's error-vs-fail handling can be tested without a
    real Runtime or provider."""

    def __init__(
        self,
        responses: list[ChatResponse] | None = None,
        errors: dict[int, Exception] | None = None,
    ) -> None:
        self._responses = list(responses or [])
        self._errors = errors or {}
        self.requests: list[ChatRequest] = []

    async def handle_chat(self, request: ChatRequest) -> ChatResponse:
        index = len(self.requests)
        self.requests.append(request)
        if index in self._errors:
            raise self._errors[index]
        return self._responses[index]
