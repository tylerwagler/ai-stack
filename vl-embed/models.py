"""
Model loading and inference for Qwen3-VL-Embedding-2B and Qwen3-VL-Reranker-2B.

Adapted from the official Qwen3-VL-Embedding repository:
https://github.com/QwenLM/Qwen3-VL-Embedding

Runs on CPU with float32 for maximum compatibility.
"""

import os
import logging
import unicodedata
import threading

import torch
import torch.nn.functional as F
import numpy as np
from dataclasses import dataclass
from typing import Optional, List, Union, Dict, Any

from PIL import Image
from urllib.parse import urlparse
from transformers.models.qwen3_vl.modeling_qwen3_vl import (
    Qwen3VLPreTrainedModel, Qwen3VLModel, Qwen3VLConfig,
)
from transformers.models.qwen3_vl.processing_qwen3_vl import Qwen3VLProcessor
from transformers.modeling_outputs import ModelOutput
from transformers import Qwen3VLForConditionalGeneration, AutoProcessor
from qwen_vl_utils.vision_process import process_vision_info

logger = logging.getLogger(__name__)

# ---------- constants ----------
MAX_LENGTH = 8192
IMAGE_BASE_FACTOR = 16
IMAGE_FACTOR = IMAGE_BASE_FACTOR * 2
MIN_PIXELS = 4 * IMAGE_FACTOR * IMAGE_FACTOR
MAX_PIXELS = 1800 * IMAGE_FACTOR * IMAGE_FACTOR
PAD_TOKEN = "<|endoftext|>"


# ================================================================
# Embedding model
# ================================================================

@dataclass
class Qwen3VLForEmbeddingOutput(ModelOutput):
    last_hidden_state: Optional[torch.FloatTensor] = None
    attention_mask: Optional[torch.Tensor] = None


class Qwen3VLForEmbedding(Qwen3VLPreTrainedModel):
    """Thin wrapper that returns hidden states instead of logits."""

    _checkpoint_conversion_mapping = {}
    accepts_loss_kwargs = False
    config: Qwen3VLConfig

    def __init__(self, config):
        super().__init__(config)
        self.model = Qwen3VLModel(config)
        self.post_init()

    def get_input_embeddings(self):
        return self.model.get_input_embeddings()

    def set_input_embeddings(self, value):
        self.model.set_input_embeddings(value)

    def forward(
        self,
        input_ids=None,
        attention_mask=None,
        position_ids=None,
        past_key_values=None,
        inputs_embeds=None,
        pixel_values=None,
        pixel_values_videos=None,
        image_grid_thw=None,
        video_grid_thw=None,
        cache_position=None,
        **kwargs,
    ):
        outputs = self.model(
            input_ids=input_ids,
            pixel_values=pixel_values,
            pixel_values_videos=pixel_values_videos,
            image_grid_thw=image_grid_thw,
            video_grid_thw=video_grid_thw,
            position_ids=position_ids,
            attention_mask=attention_mask,
            past_key_values=past_key_values,
            inputs_embeds=inputs_embeds,
            cache_position=cache_position,
            **kwargs,
        )
        return Qwen3VLForEmbeddingOutput(
            last_hidden_state=outputs.last_hidden_state,
            attention_mask=attention_mask,
        )


class EmbeddingModel:
    """Wraps Qwen3-VL-Embedding for text (and future multimodal) embeddings."""

    def __init__(self, model_name: str = "Qwen/Qwen3-VL-Embedding-2B"):
        logger.info("Loading embedding model: %s", model_name)
        self.lock = threading.Lock()

        self.model = Qwen3VLForEmbedding.from_pretrained(
            model_name, trust_remote_code=True, torch_dtype=torch.float32,
        )
        self.model.eval()

        self.processor = Qwen3VLProcessor.from_pretrained(
            model_name, padding_side="right",
        )

        self.dim = self.model.config.text_config.hidden_size
        logger.info("Embedding model loaded — dim=%d", self.dim)

    def _format_conversation(self, text: str, instruction: str = "Represent the user's input.") -> list:
        instruction = instruction.strip()
        if instruction and not unicodedata.category(instruction[-1]).startswith("P"):
            instruction += "."
        return [
            {"role": "system", "content": [{"type": "text", "text": instruction}]},
            {"role": "user", "content": [{"type": "text", "text": text}]},
        ]

    @torch.no_grad()
    def embed(self, texts: List[str]) -> List[List[float]]:
        conversations = [self._format_conversation(t) for t in texts]

        prompt_texts = self.processor.apply_chat_template(
            conversations, add_generation_prompt=True, tokenize=False,
        )

        inputs = self.processor(
            text=prompt_texts, images=None, videos=None,
            truncation=True, max_length=MAX_LENGTH,
            padding=True, return_tensors="pt",
        )

        with self.lock:
            outputs = self.model(**inputs)

        # last-token pooling
        hidden = outputs.last_hidden_state
        mask = outputs.attention_mask
        flipped = mask.flip(dims=[1])
        last_pos = flipped.argmax(dim=1)
        col = mask.shape[1] - last_pos - 1
        row = torch.arange(hidden.shape[0])
        pooled = hidden[row, col]

        # L2 normalize
        pooled = F.normalize(pooled, p=2, dim=-1)
        return pooled.tolist()


# ================================================================
# Reranker model
# ================================================================

class RerankerModel:
    """Wraps Qwen3-VL-Reranker for text (and future multimodal) reranking."""

    SYSTEM_PROMPT = (
        'Judge whether the Document meets the requirements based on the '
        'Query and the Instruct provided. Note that the answer can only '
        'be "yes" or "no".'
    )
    DEFAULT_INSTRUCTION = (
        "Given a search query, retrieve relevant candidates that answer the query."
    )

    def __init__(self, model_name: str = "Qwen/Qwen3-VL-Reranker-2B"):
        logger.info("Loading reranker model: %s", model_name)
        self.lock = threading.Lock()

        lm = Qwen3VLForConditionalGeneration.from_pretrained(
            model_name, trust_remote_code=True, torch_dtype=torch.float32,
        )

        # Keep only the backbone (discard lm_head except for yes/no weights)
        self.model = lm.model
        self.model.eval()

        self.processor = AutoProcessor.from_pretrained(
            model_name, trust_remote_code=True, padding_side="left",
        )

        # Binary classification head from yes/no token weights
        vocab = self.processor.tokenizer.get_vocab()
        w_yes = lm.lm_head.weight.data[vocab["yes"]]
        w_no = lm.lm_head.weight.data[vocab["no"]]
        self.score_linear = torch.nn.Linear(w_yes.size(0), 1, bias=False)
        with torch.no_grad():
            self.score_linear.weight[0] = w_yes - w_no
        self.score_linear.eval()

        # Free the full lm_head
        del lm
        logger.info("Reranker model loaded")

    def _format_pair(self, query: str, document: str) -> list:
        return [
            {
                "role": "system",
                "content": [{"type": "text", "text": self.SYSTEM_PROMPT}],
            },
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": f"<Instruct>: {self.DEFAULT_INSTRUCTION}"},
                    {"type": "text", "text": f"<Query>: {query}"},
                    {"type": "text", "text": f"\n<Document>: {document}"},
                ],
            },
        ]

    @torch.no_grad()
    def rerank(self, query: str, documents: List[str]) -> List[float]:
        scores = []
        for doc in documents:
            pair = self._format_pair(query, doc)

            text = self.processor.apply_chat_template(
                [pair], tokenize=False, add_generation_prompt=True,
            )
            inputs = self.processor(
                text=text, images=None, videos=None,
                truncation=True, max_length=MAX_LENGTH,
                padding=True, return_tensors="pt",
            )

            with self.lock:
                hidden = self.model(**inputs).last_hidden_state[:, -1]
                logit = self.score_linear(hidden)

            score = torch.sigmoid(logit).squeeze().item()
            scores.append(score)

        return scores
