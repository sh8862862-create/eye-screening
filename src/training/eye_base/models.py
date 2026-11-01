"""Small convolutional networks with fixed-size, ONNX-friendly inference."""
import torch
from torch import nn
from torch.nn import functional as F
from torchvision.models import MobileNet_V3_Small_Weights, mobilenet_v3_small

CLASSES = ["pinguecula", "pterygium", "subconjunctival_hemorrhage"]
LOCALIZER_CHANNELS = ["pinguecula", "pterygium"]
IMAGENET_MEAN = (0.485, 0.456, 0.406)
IMAGENET_STD = (0.229, 0.224, 0.225)


def build_classifier(pretrained=False, num_classes=3):
    """Torchvision MobileNetV3-Small, adapted to the three supplied labels."""
    weights = MobileNet_V3_Small_Weights.DEFAULT if pretrained else None
    model = mobilenet_v3_small(weights=weights)
    model.classifier[-1] = nn.Linear(model.classifier[-1].in_features, num_classes)
    return model


class SeparableBlock(nn.Sequential):
    def __init__(self, in_channels, out_channels):
        super().__init__(
            nn.Conv2d(in_channels, in_channels, 3, padding=1, groups=in_channels, bias=False),
            nn.BatchNorm2d(in_channels), nn.ReLU(inplace=True),
            nn.Conv2d(in_channels, out_channels, 1, bias=False),
            nn.BatchNorm2d(out_channels), nn.ReLU(inplace=True),
            nn.Conv2d(out_channels, out_channels, 3, padding=1, groups=out_channels, bias=False),
            nn.BatchNorm2d(out_channels), nn.ReLU(inplace=True),
            nn.Conv2d(out_channels, out_channels, 1, bias=False),
            nn.BatchNorm2d(out_channels), nn.ReLU(inplace=True),
        )


class MobileUNet(nn.Module):
    """Depthwise-separable U-Net; outputs two independent weak-region logits.

    This is a compact Mobile U-Net variant, not a pretrained segmentation
    checkpoint. Training rectangles provide region supervision only.
    """
    def __init__(self, num_classes=2):
        super().__init__()
        self.enc1 = SeparableBlock(3, 8)
        self.enc2 = SeparableBlock(8, 16)
        self.enc3 = SeparableBlock(16, 32)
        self.bottleneck = SeparableBlock(32, 64)
        self.dec3 = SeparableBlock(64 + 32, 32)
        self.dec2 = SeparableBlock(32 + 16, 16)
        self.dec1 = SeparableBlock(16 + 8, 8)
        self.out = nn.Conv2d(8, num_classes, 1)

    def forward(self, x):
        a = self.enc1(x)
        b = self.enc2(F.max_pool2d(a, 2))
        c = self.enc3(F.max_pool2d(b, 2))
        x = self.bottleneck(F.max_pool2d(c, 2))
        x = self.dec3(torch.cat((F.interpolate(x, scale_factor=2, mode="nearest"), c), 1))
        x = self.dec2(torch.cat((F.interpolate(x, scale_factor=2, mode="nearest"), b), 1))
        x = self.dec1(torch.cat((F.interpolate(x, scale_factor=2, mode="nearest"), a), 1))
        return self.out(x)


def normalize_rgb(image):
    """Convert one uint8 HWC RGB image into normalized float32 CHW tensor."""
    x = torch.as_tensor(image).permute(2, 0, 1).to(torch.float32).div(255.0)
    mean = x.new_tensor(IMAGENET_MEAN).view(3, 1, 1)
    std = x.new_tensor(IMAGENET_STD).view(3, 1, 1)
    return (x - mean) / std
