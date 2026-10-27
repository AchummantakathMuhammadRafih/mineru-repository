"""Unit tests for transformers compatibility patches."""

import pytest
import torch
import transformers
from mineru_converter.core.patches import apply_transformers_compat_patches


def test_transformers_compat_patches():
    """Verify get_head_mask exists and returns expected shapes."""
    apply_transformers_compat_patches()

    import transformers.modeling_utils as modeling_utils
    assert hasattr(modeling_utils.PreTrainedModel, "get_head_mask")
    assert hasattr(modeling_utils.PreTrainedModel, "_convert_head_mask_to_5d")

    dummy_model = modeling_utils.PreTrainedModel(transformers.PretrainedConfig())

    # Case 1: head_mask is None -> list of None
    masks = dummy_model.get_head_mask(None, num_hidden_layers=4)
    assert masks == [None, None, None, None]

    # Case 2: head_mask is 1D tensor [num_heads]
    head_mask_1d = torch.ones(8)
    masks_5d = dummy_model.get_head_mask(head_mask_1d, num_hidden_layers=4)
    assert masks_5d.dim() == 5
    assert masks_5d.shape[0] == 4
    assert masks_5d.shape[2] == 8
