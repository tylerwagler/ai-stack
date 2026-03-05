FROM vllm/vllm-openai:cu130-nightly

# Remove flash-attn if present (Ampere+ only, conflicts on Turing)
RUN pip uninstall -y flash-attn || true

# Install FlashInfer with pre-compiled cubins (includes sm_75 for Turing)
RUN pip install --no-cache-dir -U flashinfer-python flashinfer-cubin \
    && pip install --no-cache-dir -U flashinfer-jit-cache --index-url https://flashinfer.ai/whl/cu130

# Install transformers 5 from git (needed for AutoRound + newer architectures)
RUN apt-get update && apt-get install -y --no-install-recommends git \
    && pip install --no-cache-dir -U \
        git+https://github.com/huggingface/transformers.git \
        "tokenizers>=0.22.2" \
    && apt-get purge -y git && apt-get autoremove -y && rm -rf /var/lib/apt/lists/*
