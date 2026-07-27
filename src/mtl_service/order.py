"""읽기 순서 정렬. DESIGN.md §8.2.

일본 만화는 우상단에서 좌하단으로 읽는다. 이 모듈 품질이 번역 품질을 좌우한다.

**§8.2 의 컬럼 밴드 휴리스틱은 실물에서 실패해 폐기했다.** x 중심으로만 묶으면
칸(panel) 행을 가로지른다. `yanmaga_002528` 에서 상단 행의 「蜻蛉高校」와 하단 칸의
「ん？」이 x 차이 10px 이라 같은 컬럼으로 묶여, 그 사이에 와야 할 상단 행 나머지가
뒤로 밀렸다. 어떤 밴드 폭으로도 못 고친다 — 만화는 **칸 행이 1순위, 행 안에서
우→좌가 2순위**인데 그 위계를 표현할 수 없는 알고리즘이었다. 실측 51개 region 중
위치가 맞은 것이 25개(49%).

지금은 재귀 XY-cut 이다.

1. **펼침면이면 먼저 페이지로 가른다.** 가로가 세로보다 충분히 길고 가운데 1/3
   구간에 세로 빈 띠가 있으면 거기서 자르고, 오른쪽 페이지부터 각각 재귀한다.
   이게 1번인 이유는 페이지 경계가 칸 행보다 상위 구조이기 때문이다. 먼저 행으로
   자르면 두 페이지의 행이 뒤섞인다.
2. **페이지 안에서는 가로 빈 띠(= 칸 행)를 우선**한다. 위→아래로 나누고 재귀한다.
3. 가로로 못 자르면 **세로 빈 띠로 자르고 우→좌**로 재귀한다.
4. 둘 다 없으면(서로 겹친 덩어리) x 내림차순, 같으면 y 오름차순으로 떨어뜨린다.

2번을 3번보다 먼저 두는 것이 핵심이다. 한 행 안에서 좌우 끝 말풍선 사이 간격은
행 사이 간격보다 훨씬 크기 때문에, "가장 넓은 틈"으로 방향을 고르면 옛 알고리즘과
같은 실패로 돌아간다. 간격 크기가 아니라 **구조의 위계**로 방향을 정한다.

같은 8장에서 위치가 맞은 것 46/51(90%). 남은 5개는 전부 `yanmaga_002535` 한 장이고,
원인은 정렬이 아니라 검출 단계의 박스 병합이 펼침면 경계를 넘어버린 것이다
(PROGRESS.md A-2 3번).
"""

from __future__ import annotations

from mtl_shared.models import Region

#: 펼침면 판정. 가로가 세로의 이 배를 넘어야 페이지 가르기를 시도한다.
SPREAD_ASPECT = 1.2
#: 페이지 사이 홈은 가운데 이 비율 구간 안에서만 찾는다. 0.33 이면 가운데 1/3.
SPREAD_CENTER_BAND = 1 / 3


def sort_regions(
    regions: list[Region], page_size: tuple[int, int], min_gap_ratio: float
) -> list[Region]:
    """읽기 순서대로 재배열한다.

    `page_size` 는 (너비, 높이). 빈 띠 판정 문턱을 페이지 짧은 변에 걸기 때문에
    필요하다. region 들의 외접 사각형으로 대신하면 검출이 페이지 일부에만 몰렸을 때
    문턱이 같이 쪼그라들어 과분할된다.

    반환된 리스트는 새 객체가 아니라 입력 객체를 재배열한 것이다. `id` 는
    건드리지 않는다 — 부여는 호출자 몫.
    """
    if len(regions) <= 1:
        return list(regions)

    page_w, page_h = page_size
    min_gap = min_gap_ratio * min(page_w, page_h)

    if page_w > page_h * SPREAD_ASPECT:
        pages = _split_spread(regions, page_w, min_gap)
        if pages is not None:
            return [r for page in pages for r in _sort_page(page, min_gap)]

    return _sort_page(regions, min_gap)


# ---------------------------------------------------------------------------
# 펼침면 가르기
# ---------------------------------------------------------------------------


def _split_spread(
    regions: list[Region], page_w: int, min_gap: float
) -> list[list[Region]] | None:
    """가운데 홈에서 두 페이지로 가른다. 홈을 못 찾으면 None (단일 페이지 취급)."""
    lo = page_w * SPREAD_CENTER_BAND
    hi = page_w * (1 - SPREAD_CENTER_BAND)

    best = None
    for gap_start, gap_end in _gaps(regions, axis=0, min_gap=min_gap):
        mid = (gap_start + gap_end) / 2
        if not lo <= mid <= hi:
            continue
        if best is None or gap_end - gap_start > best[1] - best[0]:
            best = (gap_start, gap_end)

    if best is None:
        return None

    cut = (best[0] + best[1]) / 2
    right = [r for r in regions if r.bbox[0] + r.bbox[2] / 2 > cut]
    left = [r for r in regions if r.bbox[0] + r.bbox[2] / 2 <= cut]
    if not right or not left:
        return None
    return [right, left]  # 오른쪽 페이지 먼저


# ---------------------------------------------------------------------------
# 페이지 안 XY-cut
# ---------------------------------------------------------------------------


def _sort_page(regions: list[Region], min_gap: float) -> list[Region]:
    if len(regions) <= 1:
        return list(regions)

    # 1순위: 가로 빈 띠 = 칸 행. 위 → 아래.
    bands = _split(regions, axis=1, min_gap=min_gap, reverse=False)
    if bands is not None:
        return [r for band in bands for r in _sort_page(band, min_gap)]

    # 2순위: 세로 빈 띠. 오른쪽 → 왼쪽.
    columns = _split(regions, axis=0, min_gap=min_gap, reverse=True)
    if columns is not None:
        return [r for column in columns for r in _sort_page(column, min_gap)]

    # 서로 겹쳐서 못 가른다. 우→좌, 같으면 위→아래.
    return sorted(regions, key=lambda r: (-(r.bbox[0] + r.bbox[2] / 2), r.bbox[1]))


def _split(
    regions: list[Region], axis: int, min_gap: float, reverse: bool
) -> list[list[Region]] | None:
    """`axis` 방향 빈 띠 전부에서 쪼갠다. 쪼갤 데가 없으면 None.

    axis 0 = x(세로 띠로 좌우 분할), 1 = y(가로 띠로 상하 분할).
    """
    gaps = _gaps(regions, axis, min_gap)
    if not gaps:
        return None

    cuts = [(g[0] + g[1]) / 2 for g in gaps]
    groups: list[list[Region]] = [[] for _ in range(len(cuts) + 1)]
    for region in regions:
        start = region.bbox[axis]
        center = start + region.bbox[axis + 2] / 2
        # 어느 칸에 속하는지는 중심으로 정한다. 빈 띠 정의상 박스는 띠를 넘지 않는다.
        slot = sum(1 for cut in cuts if center > cut)
        groups[slot].append(region)

    groups = [g for g in groups if g]
    if len(groups) <= 1:
        return None
    return list(reversed(groups)) if reverse else groups


def _gaps(regions: list[Region], axis: int, min_gap: float) -> list[tuple[int, int]]:
    """`axis` 방향으로 어떤 박스도 덮지 않는 구간 중 `min_gap` 보다 넓은 것들."""
    spans = sorted(
        (r.bbox[axis], r.bbox[axis] + r.bbox[axis + 2]) for r in regions
    )

    result: list[tuple[int, int]] = []
    reach = spans[0][1]
    for start, end in spans[1:]:
        if start - reach > min_gap:
            result.append((reach, start))
        reach = max(reach, end)
    return result


def assign_ids(regions: list[Region]) -> list[Region]:
    """읽기 순서대로 1부터 번호를 붙인다. 번역·오버레이가 이 id 를 쓴다."""
    for i, region in enumerate(regions, start=1):
        region.id = i
    return regions
