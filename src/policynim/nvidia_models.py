"""Provider-independent model identities and resolved chat request settings."""

from policynim.types import ChatRequestConfiguration

NVIDIA_LIGHTNING_CHAT_MODEL = "nvidia/nemotron-3.5-lightning-30b-a3b"
DEFAULT_NVIDIA_CHAT_MODEL = NVIDIA_LIGHTNING_CHAT_MODEL


def chat_request_configuration(model: str) -> ChatRequestConfiguration:
    """Resolve the exact chat sampling settings used for this explicit model."""
    if model == NVIDIA_LIGHTNING_CHAT_MODEL:
        return ChatRequestConfiguration(
            model=model, temperature=0, top_p=1, enable_thinking=False, output_mode="json_schema"
        )
    if model == "nvidia/nemotron-3-super-120b-a12b":
        return ChatRequestConfiguration(
            model=model, temperature=1, top_p=0.95, enable_thinking=False
        )
    return ChatRequestConfiguration(model=model, temperature=0, top_p=1)
