"""comic-text-detector 추론 래퍼. GPL-3.0. NOTICE.md 참조.

전처리·후처리는 원본 `inference.py` 의 동작을 그대로 재현한다. 가중치가
그 전처리로 학습·검증된 것이라 임의로 "고치면" 정확도가 떨어진다.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
import torch

from .unet import UnetHead
from .yolov5 import load_yolov5_ckpt, non_max_suppression

#: 백본에서 뽑아 마스크 헤드로 넘길 레이어 인덱스 (stride 4/8/16/32/32)
_OUT_INDICES = [1, 3, 5, 7, 9]

#: yolo 클래스 → 언어. 말풍선 여부가 **아니다**. NOTICE.md 참조.
LANGUAGES = ("eng", "ja")


@dataclass(slots=True)
class RawDetection:
    """검출기 원출력. 좌표는 전부 입력 이미지 좌표계."""

    #: (n, 4) int32, xyxy
    boxes: np.ndarray
    #: (n,) float32
    scores: np.ndarray
    #: (n,) int32, LANGUAGES 인덱스
    lang: np.ndarray
    #: (h, w) uint8 0-255. 말풍선이 아니라 **글자 획**의 마스크다.
    mask: np.ndarray


def letterbox(
    im: np.ndarray, new_shape: tuple[int, int]
) -> tuple[np.ndarray, tuple[int, int]]:
    """종횡비를 유지해 축소하고 **우/하단에만** 검은 여백을 채운다.

    가운데 정렬 패딩이 아니다. 원본이 그렇고, 덕분에 좌상단 원점이 보존돼
    좌표 역산이 나눗셈 한 번으로 끝난다. 반환값은 (이미지, (dw, dh)).
    """
    h, w = im.shape[:2]
    r = min(new_shape[0] / h, new_shape[1] / w)
    new_w, new_h = int(round(w * r)), int(round(h * r))
    dw, dh = new_shape[1] - new_w, new_shape[0] - new_h
    if (w, h) != (new_w, new_h):
        im = cv2.resize(im, (new_w, new_h), interpolation=cv2.INTER_LINEAR)
    if dw or dh:
        im = cv2.copyMakeBorder(im, 0, dh, 0, dw, cv2.BORDER_CONSTANT, value=(0, 0, 0))
    return im, (dw, dh)


class ComicTextDetector(torch.nn.Module):
    """백본 + 텍스트 마스크 헤드. 체크포인트의 `text_det`(DB 헤드)는 쓰지 않는다."""

    def __init__(
        self,
        weights: str | Path,
        device: str = "cuda",
        input_size: int = 1024,
        act: str = "leaky",
    ):
        super().__init__()
        # 가중치는 scripts/fetch_models.py 가 sha256 을 검증하고 받는다.
        ckpt = torch.load(str(weights), map_location="cpu", weights_only=False)

        # 백본은 잘라내지 않는다. 박스(Detect 헤드)와 마스크용 중간 특징맵을
        # 한 번의 forward 로 함께 뽑는다.
        self.backbone = load_yolov5_ckpt(ckpt["blk_det"], out_indices=_OUT_INDICES)
        self.seg_net = UnetHead(act=act)
        self.seg_net.load_state_dict(ckpt["text_seg"])
        self.seg_net.eval()

        self.input_size = (input_size, input_size)
        self.device = device
        self.to(device)
        self.eval()

    def _preprocess(self, img_bgr: np.ndarray) -> tuple[torch.Tensor, tuple[int, int]]:
        padded, (dw, dh) = letterbox(img_bgr, self.input_size)
        # 원본은 BGR→RGB 변환 후 채널을 다시 뒤집는다. 상쇄돼 결국 BGR 이 들어간다.
        # 가중치가 그 순서로 학습됐으므로 BGR 그대로 넣는다.
        chw = np.ascontiguousarray(padded.transpose(2, 0, 1))
        x = torch.from_numpy(chw).to(self.device).float().div_(255).unsqueeze(0)
        return x, (dw, dh)

    @torch.inference_mode()
    def __call__(
        self,
        img_bgr: np.ndarray,
        conf_threshold: float = 0.4,
        nms_threshold: float = 0.35,
        mask_threshold: float | None = None,
    ) -> RawDetection:
        im_h, im_w = img_bgr.shape[:2]
        x, (dw, dh) = self._preprocess(img_bgr)

        blks, feats = self.backbone(x, detect=True)
        mask_t = self.seg_net(*feats)

        # --- 박스 ---
        det = non_max_suppression(blks[0], conf_threshold, nms_threshold)[0].cpu().numpy()
        ratio_x = im_w / (self.input_size[1] - dw)
        ratio_y = im_h / (self.input_size[0] - dh)
        det[..., [0, 2]] *= ratio_x
        det[..., [1, 3]] *= ratio_y
        boxes = det[..., 0:4].astype(np.int32)
        boxes[:, [0, 2]] = boxes[:, [0, 2]].clip(0, im_w)
        boxes[:, [1, 3]] = boxes[:, [1, 3]].clip(0, im_h)

        # --- 마스크 ---
        mask = mask_t.squeeze().float().cpu().numpy()
        if mask_threshold is not None:
            mask = mask > mask_threshold
        mask = (mask * 255).astype(np.uint8)
        # 레터박스 여백을 잘라낸 뒤 원본 크기로 되돌린다
        mask = mask[: mask.shape[0] - dh, : mask.shape[1] - dw]
        mask = cv2.resize(mask, (im_w, im_h), interpolation=cv2.INTER_LINEAR)

        return RawDetection(
            boxes=boxes,
            scores=det[..., 4].astype(np.float32),
            lang=det[..., 5].astype(np.int32),
            mask=mask,
        )
