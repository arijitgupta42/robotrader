import configparser
from openai import OpenAI


def load_config(path: str = "config.ini") -> configparser.ConfigParser:
    """
    Reads configuration from a .ini file.

    Parameters
    ----------
    path : str, optional
        Path to the config file. Default is 'config.ini'.

    Returns
    -------
    configparser.ConfigParser
        Parsed configuration object.
    """
    config = configparser.ConfigParser()
    config.read(path)
    return config


class ModelLoader:
    """
    Client for the Gemma 4 E2B model hosted on a RunPod
    serverless vLLM endpoint.

    Uses the OpenAI-compatible API exposed by vLLM, pointed
    at the RunPod endpoint URL. The model is remote — no local
    GPU memory is used.

    Attributes
    ----------
    endpoint_id : str
        RunPod serverless endpoint ID.
    api_key : str
        RunPod API key loaded from config.ini.
    model_id : str
        HuggingFace model identifier served by vLLM.
    client : OpenAI
        OpenAI client pointed at the RunPod vLLM endpoint.
    """

    def __init__(
        self,
        endpoint_id: str,
        config_path: str = "config.ini",
        model_id: str = "google/gemma-4-E2B-it",
    ):
        """
        Parameters
        ----------
        endpoint_id : str
            RunPod serverless endpoint ID (e.g. 'abc123xyz').
        config_path : str, optional
            Path to the config.ini file containing the RunPod
            API key. Default is 'config.ini'.
        model_id : str, optional
            HuggingFace model identifier served by the vLLM
            endpoint. Default is 'google/gemma-4-E2B-it'.
        """
        config = load_config(config_path)
        self.api_key     = config["runpod"]["api_key"]
        self.endpoint_id = endpoint_id
        self.model_id    = model_id

        self.client = OpenAI(
            api_key=self.api_key,
            base_url=f"https://api.runpod.ai/v2/{self.endpoint_id}/openai/v1",
        )

    def generate(self, messages: list[dict], max_new_tokens: int = 512) -> str:
        """
        Runs inference against the RunPod vLLM endpoint.

        Parameters
        ----------
        messages : list of dict
            Chat-formatted messages. Each dict must have
            'role' and 'content' keys. Role must be one of
            'system', 'user', or 'assistant'.
        max_new_tokens : int, optional
            Maximum number of tokens to generate. Default is 512.

        Returns
        -------
        str
            The raw generated text response from the model.
        """
        response = self.client.chat.completions.create(
            model=self.model_id,
            messages=messages,
            max_tokens=max_new_tokens,
            temperature=0,      # deterministic — important for consistency
        )
        return response.choices[0].message.content