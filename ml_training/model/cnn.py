"""Tiny 1-D CNN compatible with the existing board inference contract.

Architecture is the frozen ``tiny-iq-cnn-v1`` network
(``2 -> 8 -> 16 -> 16`` conv1d stride 2, ReLU, global average pool, linear head),
re-used here with a 4-class head.  Input is the ``dc-complex-rms-v1``
pre-processed ``[2, 4096]`` float32 tensor; the head input is the 16-wide global
average pool, exactly like the board runtime.
"""
from __future__ import annotations

import numpy as np

INPUT_SHAPE = (2, 4096)
WEIGHT_SHAPES_4 = {
    "conv1.weight": (8, 2, 7), "conv1.bias": (8,),
    "conv2.weight": (16, 8, 5), "conv2.bias": (16,),
    "conv3.weight": (16, 16, 3), "conv3.bias": (16,),
    "head.weight": (4, 16), "head.bias": (4,),
}
NUM_CLASSES = 4
TORCH_TO_CONTRACT = {
    "body.0.weight": "conv1.weight", "body.0.bias": "conv1.bias",
    "body.2.weight": "conv2.weight", "body.2.bias": "conv2.bias",
    "body.4.weight": "conv3.weight", "body.4.bias": "conv3.bias",
    "head.weight": "head.weight", "head.bias": "head.bias",
}


def build_network(num_classes=NUM_CLASSES):
    """Return the fixed TinyIQCNN torch module (raises ImportError without torch)."""
    import torch
    import torch.nn as nn

    class TinyIQCNN(nn.Module):
        def __init__(self):
            super().__init__()
            self.body = nn.Sequential(
                nn.Conv1d(2, 8, 7, stride=2, padding=3), nn.ReLU(),
                nn.Conv1d(8, 16, 5, stride=2, padding=2), nn.ReLU(),
                nn.Conv1d(16, 16, 3, stride=2, padding=1), nn.ReLU())
            self.head = nn.Linear(16, num_classes)

        def forward(self, x):
            return self.head(self.body(x).mean(dim=-1))

    return TinyIQCNN()


def parameter_count(model):
    return int(sum(p.numel() for p in model.parameters()))


def macs_for_length(input_length=4096, num_classes=NUM_CLASSES):
    """Multiply-accumulate count for one frame (stride-2 conv stack + linear head)."""
    length = input_length
    channels = 2
    total = 0
    for out_channels, kernel in ((8, 7), (16, 5), (16, 3)):
        length = (length + 2 * (kernel // 2) - kernel) // 2 + 1
        total += length * out_channels * channels * kernel
        channels = out_channels
    total += channels * num_classes
    return int(total)


def state_to_weights(state_dict):
    """Convert a torch state_dict into the contract weight dict (numpy float32)."""
    weights = {}
    for source, target in TORCH_TO_CONTRACT.items():
        if source not in state_dict:
            raise ValueError("state_dict missing " + source)
        tensor = state_dict[source].detach().cpu().numpy().astype(np.float32)
        expected = WEIGHT_SHAPES_4[target]
        if tuple(tensor.shape) != expected:
            raise ValueError("weight %s has shape %r, expected %r"
                             % (target, tuple(tensor.shape), expected))
        weights[target] = tensor
    return weights


def weights_to_document(weights):
    document = {}
    for name, array in weights.items():
        document[name] = {"shape": list(array.shape),
                          "values": [float(value) for value in array.reshape(-1)]}
    return document
