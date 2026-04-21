import torch
import logging
from transformers import AutoProcessor, AutoModelForMultimodalLM, BitsAndBytesConfig

logger = logging.getLogger(__name__)


class ModelLoader:
    """
    Centralised loader for the Gemma 4 E2B model.

    Loads the model once and shares it across all SLM agents
    to avoid redundant memory allocation. Uses 4-bit quantisation
    to fit within an 8GB VRAM budget (RTX 3070).

    Attributes
    ----------
    model_id : str
        HuggingFace model identifier.
    model : AutoModelForMultimodalLM or None
        Loaded model instance. None until load() is called.
    processor : AutoProcessor or None
        Loaded processor instance. None until load() is called.
    """

    def __init__(self, model_id: str = "google/gemma-4-E2B-it"):
        """
        Parameters
        ----------
        model_id : str, optional
            HuggingFace model identifier. Default is 'google/gemma-4-E2B-it'.
        """
        self.model_id  = model_id
        self.model     = None
        self.processor = None

    def load(self) -> None:
        """
        Downloads and loads the model and processor into memory.

        Uses 4-bit quantisation (NF4) via bitsandbytes to fit within
        8GB VRAM. Safe to call multiple times — skips loading if the
        model is already in memory.

        Returns
        -------
        None
        """
        if self.model is not None:
            logger.info("Model already loaded, skipping.")
            return

        logger.info(f"Loading {self.model_id} with 4-bit quantisation...")

        quant_config = BitsAndBytesConfig(
            load_in_4bit=True,
            bnb_4bit_compute_dtype=torch.bfloat16,
            bnb_4bit_use_double_quant=True,
            bnb_4bit_quant_type="nf4",
        )

        self.model = AutoModelForMultimodalLM.from_pretrained(
            self.model_id,
            quantization_config=quant_config,
            device_map="auto",
        ).eval()

        self.processor = AutoProcessor.from_pretrained(self.model_id)

        logger.info("Model loaded successfully.")

    def generate(self, messages: list[dict], max_new_tokens: int = 512) -> str:
        """
        Runs inference for a single list of messages.

        Parameters
        ----------
        messages : list of dict
            Chat-formatted messages following the Gemma 4 template.
            Each dict must have 'role' and 'content' keys.
        max_new_tokens : int, optional
            Maximum number of tokens to generate. Default is 512.

        Returns
        -------
        str
            The raw generated text response from the model.
        """
        inputs = self.processor.apply_chat_template(
            messages,
            tokenize=True,
            add_generation_prompt=True,
            return_dict=True,
            return_tensors="pt",
        ).to(self.model.device)

        input_len = inputs["input_ids"].shape[-1]

        with torch.inference_mode():
            outputs = self.model.generate(
                **inputs,
                max_new_tokens=max_new_tokens,
                do_sample=False,        # deterministic — important for consistency
            )

        response = outputs[0][input_len:]
        return self.processor.decode(response, skip_special_tokens=True)