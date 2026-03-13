FROM vllm/vllm-openai:cu130-nightly

# Remove flash-attn if present (Ampere+ only, conflicts on Turing)
RUN pip uninstall -y flash-attn || true

# Install FlashInfer (includes sm_75 for Turing)
RUN pip install --no-cache-dir -U flashinfer-python flashinfer-cubin \
    && pip install --no-cache-dir -U flashinfer-jit-cache --index-url https://flashinfer.ai/whl/cu130

# Install latest transformers (needed for AutoRound + newer architectures)
RUN pip install --no-cache-dir -U transformers fastsafetensors
