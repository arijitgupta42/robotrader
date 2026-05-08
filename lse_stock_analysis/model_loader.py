import httpx
import logging

logger = logging.getLogger(__name__)

_OPENROUTER_BASE = "https://openrouter.ai/api/v1/chat/completions"
_DEFAULT_MODEL   = "google/gemma-2-9b-it"   # closest freely-available Gemma on OpenRouter


class ModelLoader:
    """
    Thin wrapper around the OpenRouter chat-completions API.

    Replaces the previous local HuggingFace/bitsandbytes loader.
    Keeps the same public interface (load / generate) so all downstream
    agents work without modification.

    Attributes
    ----------
    model_id : str
        OpenRouter model string, e.g. "google/gemma-2-9b-it".
    api_key : str
        OpenRouter API key (Bearer token).
    timeout : float
        Per-request HTTP timeout in seconds.
    _ready : bool
        True after load() has been called.
    """

    def __init__(
        self,
        api_key:  str,
        model_id: str   = _DEFAULT_MODEL,
        timeout:  float = 60.0,
    ):
        """
        Parameters
        ----------
        api_key : str
            Your OpenRouter API key. Required.
        model_id : str, optional
            OpenRouter model identifier. Defaults to 'google/gemma-2-9b-it'.
            See https://openrouter.ai/models for the full list.
        timeout : float, optional
            HTTP timeout per request in seconds. Default 60.
        """
        self.api_key  = api_key
        self.model_id = model_id
        self.timeout  = timeout
        self._ready   = False

    # ------------------------------------------------------------------
    # Public interface (mirrors the old HuggingFace-based ModelLoader)
    # ------------------------------------------------------------------

    def load(self) -> None:
        """
        'Load' the model — for an API-backed loader this just validates
        that an API key has been supplied and marks the loader as ready.

        Safe to call multiple times.
        """
        if self._ready:
            logger.info("ModelLoader already ready, skipping.")
            return

        if not self.api_key:
            raise ValueError(
                "OpenRouter API key is required. "
                "Set OPENROUTER_API_KEY in main.py or pass it to ModelLoader()."
            )

        logger.info(
            f"ModelLoader initialised → OpenRouter / {self.model_id}"
        )
        self._ready = True

    def generate(self, messages: list[dict], max_new_tokens: int = 512) -> str:
        """
        Send a chat completion request to OpenRouter and return the
        assistant's reply as a plain string.

        Parameters
        ----------
        messages : list of dict
            OpenAI-style chat messages:
            [{"role": "system"|"user"|"assistant", "content": str}, ...]

            The old Gemma-style content format (list of {"type","text"} dicts)
            is normalised automatically so ReturnProjectionAgent needs no changes.
        max_new_tokens : int, optional
            Maps to the ``max_tokens`` parameter in the API request.

        Returns
        -------
        str
            Raw text of the assistant's reply.

        Raises
        ------
        RuntimeError
            If load() has not been called, or the API returns a non-200
            status code.
        """
        if not self._ready:
            raise RuntimeError("Call load() before generate().")

        # Normalise messages: the old SLM used {"content": [{"type":"text","text":...}]}
        # OpenRouter expects {"content": str}
        normalised = []
        for msg in messages:
            content = msg["content"]
            if isinstance(content, list):
                # Flatten list-of-text-blocks into a single string
                content = "\n".join(
                    block["text"] for block in content if block.get("type") == "text"
                )
            normalised.append({"role": msg["role"], "content": content})

        payload = {
            "model"      : self.model_id,
            "messages"   : normalised,
            "max_tokens" : max_new_tokens,
            "temperature": 0.0,   # deterministic — mirrors do_sample=False
        }

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type" : "application/json",
        }

        logger.debug(f"POST {_OPENROUTER_BASE} model={self.model_id}")

        response = httpx.post(
            _OPENROUTER_BASE,
            json    = payload,
            headers = headers,
            timeout = self.timeout,
        )

        if response.status_code != 200:
            raise RuntimeError(
                f"OpenRouter API error {response.status_code}: {response.text}"
            )

        data = response.json()
        return data["choices"][0]["message"]["content"]
