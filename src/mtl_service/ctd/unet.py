"""comic-text-detector 의 텍스트 마스크 헤드 (`text_seg`). GPL-3.0. NOTICE.md 참조.

yolov5 백본의 중간 특징맵 5개를 받아 입력 해상도의 **텍스트 픽셀 마스크**를
1채널 sigmoid 로 내놓는다. 말풍선 마스크가 아니라 글자 획의 마스크다 —
이 구분이 `fill_rgb` 계산(마스크 외곽 = 말풍선 바탕색)의 전제다.

속성 이름(`down_conv1`, `upconv0`, `upconv2`~`upconv6`, 내부 `conv`)은
체크포인트의 state_dict 키와 1:1로 대응한다. 바꾸면 로드가 깨진다.
"""

from __future__ import annotations

import torch
import torch.nn as nn

from .yolov5 import C3


class double_conv_c3(nn.Module):  # noqa: N801 (체크포인트 키에 맞춘 원본 이름)
    def __init__(self, in_ch: int, out_ch: int, stride: int = 1, act: bool | str = True):
        super().__init__()
        self.down = nn.AvgPool2d(2, stride=2) if stride > 1 else None
        self.conv = C3(in_ch, out_ch, act=act)

    def forward(self, x):
        if self.down is not None:
            x = self.down(x)
        return self.conv(x)


class double_conv_up_c3(nn.Module):  # noqa: N801
    def __init__(self, in_ch: int, mid_ch: int, out_ch: int, act: bool | str = True):
        super().__init__()
        self.conv = nn.Sequential(
            C3(in_ch + mid_ch, mid_ch, act=act),
            nn.ConvTranspose2d(mid_ch, out_ch, kernel_size=4, stride=2, padding=1, bias=False),
            nn.BatchNorm2d(out_ch),
            nn.ReLU(inplace=True),
        )

    def forward(self, x):
        return self.conv(x)


class UnetHead(nn.Module):
    """백본 특징맵 → 텍스트 마스크.

    forward 인자 이름의 숫자는 원본 기준(640px 입력)의 특징맵 해상도다.
    실제 입력이 1024px면 그만큼 커진다. 순서만 맞으면 된다.
    """

    def __init__(self, act: bool | str = True):
        super().__init__()
        self.down_conv1 = double_conv_c3(512, 512, 2, act=act)
        self.upconv0 = double_conv_up_c3(0, 512, 256, act=act)
        self.upconv2 = double_conv_up_c3(256, 512, 256, act=act)
        self.upconv3 = double_conv_up_c3(0, 512, 256, act=act)
        self.upconv4 = double_conv_up_c3(128, 256, 128, act=act)
        self.upconv5 = double_conv_up_c3(64, 128, 64, act=act)
        self.upconv6 = nn.Sequential(
            nn.ConvTranspose2d(64, 1, kernel_size=4, stride=2, padding=1, bias=False),
            nn.Sigmoid(),
        )

    def forward(self, f160, f80, f40, f20, f3) -> torch.Tensor:
        d10 = self.down_conv1(f3)
        u20 = self.upconv0(d10)
        u40 = self.upconv2(torch.cat([f20, u20], dim=1))
        u80 = self.upconv3(torch.cat([f40, u40], dim=1))
        u160 = self.upconv4(torch.cat([f80, u80], dim=1))
        u320 = self.upconv5(torch.cat([f160, u160], dim=1))
        return self.upconv6(u320)
