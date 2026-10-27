"""Compatibility patches for third-party libraries (Transformers, MinerU, PyTorch)."""

import logging

logger = logging.getLogger("mineru_converter")


def apply_transformers_compat_patches() -> None:
    """
    Patch missing `get_head_mask` in transformers >= 4.45 / 5.x.
    UniMERNet's Swin transformer (used for MinerU formula recognition) calls
    `self.get_head_mask(head_mask, len(self.config.depths))`.
    In newer transformers releases, `get_head_mask` was removed from ModuleUtilsMixin,
    causing: AttributeError: 'UnimerSwinModel' object has no attribute 'get_head_mask'.
    """
    try:
        import transformers.modeling_utils as modeling_utils
    except ImportError:
        return

    def _convert_head_mask_to_5d(self, head_mask, num_hidden_layers):
        """-> [num_hidden_layers x batch x num_heads x seq_length x seq_length]"""
        if hasattr(head_mask, "dim"):
            if head_mask.dim() == 1:
                head_mask = head_mask.unsqueeze(0).unsqueeze(0).unsqueeze(-1).unsqueeze(-1)
                head_mask = head_mask.expand(num_hidden_layers, -1, -1, -1, -1)
            elif head_mask.dim() == 2:
                head_mask = head_mask.unsqueeze(1).unsqueeze(-1).unsqueeze(-1)
        return head_mask

    def get_head_mask(self, head_mask=None, num_hidden_layers=1, is_attention_chunked=False):
        """Prepare head mask for transformer attention layers."""
        if head_mask is not None:
            if hasattr(self, "_convert_head_mask_to_5d"):
                head_mask = self._convert_head_mask_to_5d(head_mask, num_hidden_layers)
            else:
                head_mask = _convert_head_mask_to_5d(self, head_mask, num_hidden_layers)
            if is_attention_chunked and hasattr(head_mask, "unsqueeze"):
                head_mask = head_mask.unsqueeze(-1)
        else:
            head_mask = [None] * num_hidden_layers
        return head_mask

    # Patch ModuleUtilsMixin if missing
    if hasattr(modeling_utils, "ModuleUtilsMixin"):
        if not hasattr(modeling_utils.ModuleUtilsMixin, "get_head_mask"):
            modeling_utils.ModuleUtilsMixin.get_head_mask = get_head_mask
        if not hasattr(modeling_utils.ModuleUtilsMixin, "_convert_head_mask_to_5d"):
            modeling_utils.ModuleUtilsMixin._convert_head_mask_to_5d = _convert_head_mask_to_5d

    # Patch PreTrainedModel if missing
    if hasattr(modeling_utils, "PreTrainedModel"):
        if not hasattr(modeling_utils.PreTrainedModel, "get_head_mask"):
            modeling_utils.PreTrainedModel.get_head_mask = get_head_mask
        if not hasattr(modeling_utils.PreTrainedModel, "_convert_head_mask_to_5d"):
            modeling_utils.PreTrainedModel._convert_head_mask_to_5d = _convert_head_mask_to_5d

    # Directly patch UnimerSwinModel if mineru can be imported
    try:
        from mineru.model.mfr.unimernet.unimernet_hf.unimer_swin.modeling_unimer_swin import (
            UnimerSwinModel,
        )

        if not hasattr(UnimerSwinModel, "get_head_mask"):
            UnimerSwinModel.get_head_mask = get_head_mask
        if not hasattr(UnimerSwinModel, "_convert_head_mask_to_5d"):
            UnimerSwinModel._convert_head_mask_to_5d = _convert_head_mask_to_5d
    except Exception:
        pass


# Apply immediately upon module load
apply_transformers_compat_patches()
