FROM vllm/vllm-openai:cu126

# Qwen3-Coder-30B doesn't need FlashInfer MLA patches (uses MHA/MQA, not MLA)
# Remove flash-attn if present (Ampere+ only, may conflict)
RUN pip uninstall -y flash-attn || true

# Install FlashInfer with pre-compiled cubins (includes sm_75 for Turing)
RUN pip install --no-cache-dir -U flashinfer-python flashinfer-cubin \
    && pip install --no-cache-dir -U flashinfer-jit-cache --index-url https://flashinfer.ai/whl/cu126
