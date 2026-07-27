"""YOLOv5 모듈 정의 — comic-text-detector 체크포인트의 `blk_det` 로드용.

ultralytics/yolov5 v6.x 발췌. GPL-3.0. NOTICE.md 참조.

체크포인트 cfg 가 실제로 참조하는 모듈만 남겼다. 다른 모듈이 등장하면
`parse_model` 이 예외를 낸다 — 조용히 다른 구조로 조립되는 것보다 낫다.
"""

from __future__ import annotations

import math
from copy import deepcopy
from typing import Any

import torch
import torch.nn as nn
import torchvision


# --------------------------------------------------------------------------
# 유틸
# --------------------------------------------------------------------------


def autopad(k, p=None):
    if p is None:
        p = k // 2 if isinstance(k, int) else [x // 2 for x in k]
    return p


def make_divisible(x: float, divisor: int) -> int:
    return math.ceil(x / divisor) * divisor


def check_anchor_order(m: "Detect") -> None:
    """앵커 순서를 stride 순서와 맞춘다."""
    a = m.anchors.prod(-1).view(-1)
    if (a[-1] - a[0]).sign() != (m.stride[-1] - m.stride[0]).sign():
        m.anchors[:] = m.anchors.flip(0)


def initialize_weights(model: nn.Module) -> None:
    for m in model.modules():
        t = type(m)
        if t is nn.BatchNorm2d:
            m.eps = 1e-3
            m.momentum = 0.03
        elif t in (nn.Hardswish, nn.LeakyReLU, nn.ReLU, nn.ReLU6, nn.SiLU):
            m.inplace = True


@torch.no_grad()
def fuse_conv_and_bn(conv: nn.Conv2d, bn: nn.BatchNorm2d) -> nn.Conv2d:
    """Conv+BN 을 하나의 Conv 로 접는다. 추론 전용."""
    fused = (
        nn.Conv2d(
            conv.in_channels,
            conv.out_channels,
            kernel_size=conv.kernel_size,
            stride=conv.stride,
            padding=conv.padding,
            groups=conv.groups,
            bias=True,
        )
        .requires_grad_(False)
        .to(conv.weight.device)
    )
    w_conv = conv.weight.clone().view(conv.out_channels, -1)
    w_bn = torch.diag(bn.weight.div(torch.sqrt(bn.eps + bn.running_var)))
    fused.weight.copy_(torch.mm(w_bn, w_conv).view(fused.weight.shape))

    b_conv = (
        torch.zeros(conv.weight.size(0), device=conv.weight.device)
        if conv.bias is None
        else conv.bias
    )
    b_bn = bn.bias - bn.weight.mul(bn.running_mean).div(torch.sqrt(bn.running_var + bn.eps))
    fused.bias.copy_(torch.mm(w_bn, b_conv.reshape(-1, 1)).reshape(-1) + b_bn)
    return fused


def xywh2xyxy(x: torch.Tensor) -> torch.Tensor:
    y = x.clone()
    y[:, 0] = x[:, 0] - x[:, 2] / 2
    y[:, 1] = x[:, 1] - x[:, 3] / 2
    y[:, 2] = x[:, 0] + x[:, 2] / 2
    y[:, 3] = x[:, 1] + x[:, 3] / 2
    return y


def non_max_suppression(
    prediction: torch.Tensor,
    conf_thres: float = 0.4,
    iou_thres: float = 0.35,
    max_det: int = 300,
) -> list[torch.Tensor]:
    """추론 결과에 NMS. 이미지당 (n, 6) 텐서 [xyxy, conf, cls] 를 돌려준다.

    원본에서 학습·오토라벨링용 경로(labels / multi_label / merge-NMS)를 뺐다.
    """
    max_wh = 4096  # 클래스별로 박스를 이 거리만큼 밀어 NMS 를 클래스 단위로 만든다
    max_nms = 30000

    candidates = prediction[..., 4] > conf_thres
    output = [prediction.new_zeros((0, 6))] * prediction.shape[0]

    for xi, x in enumerate(prediction):
        x = x[candidates[xi]]
        if not x.shape[0]:
            continue

        x[:, 5:] *= x[:, 4:5]  # conf = obj_conf * cls_conf
        box = xywh2xyxy(x[:, :4])
        conf, cls = x[:, 5:].max(1, keepdim=True)
        x = torch.cat((box, conf, cls.float()), 1)[conf.view(-1) > conf_thres]

        n = x.shape[0]
        if not n:
            continue
        if n > max_nms:
            x = x[x[:, 4].argsort(descending=True)[:max_nms]]

        offset = x[:, 5:6] * max_wh
        keep = torchvision.ops.nms(x[:, :4] + offset, x[:, 4], iou_thres)[:max_det]
        output[xi] = x[keep]

    return output


# --------------------------------------------------------------------------
# 모듈
# --------------------------------------------------------------------------


class Conv(nn.Module):
    """Conv + BN + activation."""

    def __init__(self, c1, c2, k=1, s=1, p=None, g=1, act: bool | str = True):
        super().__init__()
        self.conv = nn.Conv2d(c1, c2, k, s, autopad(k, p), groups=g, bias=False)
        self.bn = nn.BatchNorm2d(c2)
        if isinstance(act, str):
            if act == "leaky":
                self.act: nn.Module = nn.LeakyReLU(0.1, inplace=True)
            elif act == "relu":
                self.act = nn.ReLU(inplace=True)
            else:
                raise ValueError(f"알 수 없는 activation: {act!r}")
        else:
            self.act = nn.SiLU() if act is True else nn.Identity()

    def forward(self, x):
        return self.act(self.bn(self.conv(x)))

    def forward_fuse(self, x):
        return self.act(self.conv(x))


class Bottleneck(nn.Module):
    def __init__(self, c1, c2, shortcut=True, g=1, e=0.5, act=True):
        super().__init__()
        c_ = int(c2 * e)
        self.cv1 = Conv(c1, c_, 1, 1, act=act)
        self.cv2 = Conv(c_, c2, 3, 1, g=g, act=act)
        self.add = shortcut and c1 == c2

    def forward(self, x):
        return x + self.cv2(self.cv1(x)) if self.add else self.cv2(self.cv1(x))


class C3(nn.Module):
    """3-conv CSP bottleneck."""

    def __init__(self, c1, c2, n=1, shortcut=True, g=1, e=0.5, act=True):
        super().__init__()
        c_ = int(c2 * e)
        self.cv1 = Conv(c1, c_, 1, 1, act=act)
        self.cv2 = Conv(c1, c_, 1, 1, act=act)
        self.cv3 = Conv(2 * c_, c2, 1, act=act)
        self.m = nn.Sequential(
            *(Bottleneck(c_, c_, shortcut, g, e=1.0, act=act) for _ in range(n))
        )

    def forward(self, x):
        return self.cv3(torch.cat((self.m(self.cv1(x)), self.cv2(x)), dim=1))


class SPPF(nn.Module):
    """Spatial Pyramid Pooling - Fast."""

    def __init__(self, c1, c2, k=5):
        super().__init__()
        c_ = c1 // 2
        self.cv1 = Conv(c1, c_, 1, 1)
        self.cv2 = Conv(c_ * 4, c2, 1, 1)
        self.m = nn.MaxPool2d(kernel_size=k, stride=1, padding=k // 2)

    def forward(self, x):
        x = self.cv1(x)
        y1 = self.m(x)
        y2 = self.m(y1)
        return self.cv2(torch.cat([x, y1, y2, self.m(y2)], 1))


class Concat(nn.Module):
    def __init__(self, dimension=1):
        super().__init__()
        self.d = dimension

    def forward(self, x):
        return torch.cat(x, self.d)


class Detect(nn.Module):
    stride: torch.Tensor | None = None

    def __init__(self, nc=80, anchors=(), ch=(), inplace=True):
        super().__init__()
        self.nc = nc
        self.no = nc + 5
        self.nl = len(anchors)
        self.na = len(anchors[0]) // 2
        self.grid = [torch.zeros(1)] * self.nl
        self.anchor_grid = [torch.zeros(1)] * self.nl
        self.register_buffer("anchors", torch.tensor(anchors).float().view(self.nl, -1, 2))
        self.m = nn.ModuleList(nn.Conv2d(x, self.no * self.na, 1) for x in ch)
        self.inplace = inplace

    def forward(self, x):
        z = []
        for i in range(self.nl):
            x[i] = self.m[i](x[i])
            bs, _, ny, nx = x[i].shape
            x[i] = x[i].view(bs, self.na, self.no, ny, nx).permute(0, 1, 3, 4, 2).contiguous()

            if not self.training:
                if self.grid[i].shape[2:4] != x[i].shape[2:4]:
                    self.grid[i], self.anchor_grid[i] = self._make_grid(nx, ny, i)
                y = x[i].sigmoid()
                y[..., 0:2] = (y[..., 0:2] * 2 - 0.5 + self.grid[i]) * self.stride[i]
                y[..., 2:4] = (y[..., 2:4] * 2) ** 2 * self.anchor_grid[i]
                z.append(y.view(bs, -1, self.no))

        # 학습 모드에서는 리스트를 그대로 돌려준다. Model.__init__ 의 stride 산출이
        # 이 경로에 의존하므로 지우지 말 것.
        return x if self.training else (torch.cat(z, 1), x)

    def _make_grid(self, nx=20, ny=20, i=0):
        d = self.anchors[i].device
        yv, xv = torch.meshgrid(
            torch.arange(ny, device=d), torch.arange(nx, device=d), indexing="ij"
        )
        grid = torch.stack((xv, yv), 2).expand((1, self.na, ny, nx, 2)).float()
        anchor_grid = (
            (self.anchors[i].clone() * self.stride[i])
            .view((1, self.na, 1, 1, 2))
            .expand((1, self.na, ny, nx, 2))
            .float()
        )
        return grid, anchor_grid


#: cfg 의 모듈 이름 → 클래스. 여기 없는 이름이 나오면 parse_model 이 예외를 낸다.
_MODULES: dict[str, Any] = {
    "Conv": Conv,
    "Bottleneck": Bottleneck,
    "C3": C3,
    "SPPF": SPPF,
    "Concat": Concat,
    "Detect": Detect,
    "nn.Upsample": nn.Upsample,
}

#: 채널 수에 width_multiple 을 먹이는 모듈들
_WIDTH_SCALED = (Conv, Bottleneck, C3, SPPF)
#: 반복 수(n)를 인자로 받는 모듈들
_REPEATED = (C3,)


def _eval_arg(a: Any, ctx: dict[str, Any]) -> Any:
    """cfg 의 문자열 인자를 값으로 바꾼다.

    yolov5 cfg 는 `"None"`, `"nc"`, `"anchors"`, `"nearest"` 처럼 값과 이름과
    리터럴 문자열이 섞여 있다. 원본은 `eval()` 로 처리했다. 여기서는 필요한
    것만 명시적으로 해석하고 나머지는 문자열 그대로 둔다.
    """
    if not isinstance(a, str):
        return a
    if a == "None":
        return None
    return ctx.get(a, a)


def parse_model(d: dict[str, Any], ch: list[int]) -> tuple[nn.Sequential, list[int]]:
    anchors, nc = d["anchors"], d["nc"]
    gd, gw = d["depth_multiple"], d["width_multiple"]
    na = (len(anchors[0]) // 2) if isinstance(anchors, list) else anchors
    no = na * (nc + 5)
    ctx = {"nc": nc, "anchors": anchors}

    layers: list[nn.Module] = []
    save: list[int] = []
    c2 = ch[-1]

    for i, (f, n, m_name, args) in enumerate(d["backbone"] + d["head"]):
        if m_name not in _MODULES:
            raise ValueError(
                f"cfg 레이어 {i} 의 모듈 {m_name!r} 은 벤더링 대상이 아니다. "
                f"ctd/yolov5.py 의 _MODULES 에 추가할 것."
            )
        m = _MODULES[m_name]
        args = [_eval_arg(a, ctx) for a in args]

        n = max(round(n * gd), 1) if n > 1 else n  # depth gain

        if m in _WIDTH_SCALED:
            c1, c2 = ch[f], args[0]
            if c2 != no:
                c2 = make_divisible(c2 * gw, 8)
            args = [c1, c2, *args[1:]]
            if m in _REPEATED:
                args.insert(2, n)
                n = 1
        elif m is Concat:
            c2 = sum(ch[x] for x in f)
        elif m is Detect:
            args.append([ch[x] for x in f])
        else:  # nn.Upsample 등 채널을 바꾸지 않는 모듈
            c2 = ch[f]

        module = nn.Sequential(*(m(*args) for _ in range(n))) if n > 1 else m(*args)
        module.i, module.f = i, f
        save.extend(x % i for x in ([f] if isinstance(f, int) else f) if x != -1)
        layers.append(module)
        if i == 0:
            ch = []
        ch.append(c2)

    return nn.Sequential(*layers), sorted(save)


class Model(nn.Module):
    """yolov5 본체. `out_indices` 를 주면 중간 특징맵도 함께 내보낸다."""

    def __init__(self, cfg: dict[str, Any], ch: int = 3):
        super().__init__()
        self.out_indices: list[int] | None = None
        self.yaml = cfg
        ch = self.yaml["ch"] = self.yaml.get("ch", ch)
        self.model, self.save = parse_model(deepcopy(self.yaml), ch=[ch])
        self.inplace = self.yaml.get("inplace", True)

        m = self.model[-1]
        assert isinstance(m, Detect), "cfg 마지막 레이어가 Detect 가 아니다"
        s = 256  # 최소 stride 의 2배
        m.inplace = self.inplace
        # 여기서는 아직 training 모드라 Detect 가 특징맵 리스트를 돌려준다.
        m.stride = torch.tensor([s / x.shape[-2] for x in self.forward(torch.zeros(1, ch, s, s))])
        m.anchors /= m.stride.view(-1, 1, 1)
        check_anchor_order(m)
        self.stride = m.stride

        initialize_weights(self)

    def forward(self, x, detect: bool = False):
        y: list[Any] = []
        z: list[torch.Tensor] = []
        for m in self.model:
            if m.f != -1:
                x = y[m.f] if isinstance(m.f, int) else [x if j == -1 else y[j] for j in m.f]
            x = m(x)
            y.append(x if m.i in self.save else None)
            if self.out_indices is not None and m.i in self.out_indices:
                z.append(x)

        if self.out_indices is None:
            return x
        return (x, z) if detect else z

    def fuse(self) -> "Model":
        for m in self.model.modules():
            if isinstance(m, Conv) and hasattr(m, "bn"):
                m.conv = fuse_conv_and_bn(m.conv, m.bn)
                delattr(m, "bn")
                m.forward = m.forward_fuse
        return self


@torch.no_grad()
def load_yolov5_ckpt(
    ckpt: dict[str, Any],
    *,
    fuse: bool = True,
    out_indices: list[int] | None = None,
) -> Model:
    """`{'cfg': ..., 'weights': ...}` 형태의 체크포인트에서 Model 을 만든다."""
    model = Model(ckpt["cfg"])
    model.load_state_dict(ckpt["weights"], strict=True)
    model = model.float().eval()
    if fuse:
        model = model.fuse()
    model.out_indices = out_indices
    return model
