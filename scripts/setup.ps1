#Requires -Version 5.1
<#
    MangaLiveReader 부트스트랩 런처. `MangaLiveReader.cmd` 가 이 파일을 부른다.

    없는 것만 채우고 서비스를 띄운다. 모든 단계가 멱등이라 두 번째 실행부터는
    전부 「있음」으로 넘어가고 몇 초 만에 뜬다. 설치용과 실행용을 나누지 않는
    이유다 — 받는 사람은 늘 같은 파일 하나만 누르면 된다.

    **이 파일은 UTF-8 BOM 으로 저장한다.** PowerShell 5.1 은 BOM 이 없으면
    스크립트를 ANSI(한국어 윈도우는 cp949)로 읽어 한글이 깨진다. 편집기가
    BOM 을 떼면 다시 붙일 것.

    GPU 갈래:

      NVIDIA  → pyproject 의 cu128 인덱스 그대로. 약 7GB.
      그 외   → torch/torchvision 만 빼고 sync 한 뒤 CPU 휠로 따로 깐다. 약 500MB.
                라데온은 이 길밖에 없다 — ROCm 은 리눅스 전용이고 윈도우
                DirectML 은 옛 torch 에 묶여 있어 이 프로젝트가 못 쓴다.

    고른 갈래는 `.setup-mode` 에 적어 둔다. 다음 실행 때 같은 조합으로 sync
    해야 하기 때문이다 — CPU 로 깐 기계에서 맨 `uv sync` 를 돌리면 cu128 이
    다시 6GB 를 끌고 들어온다.
#>

param(
    # 설치 확인을 건너뛰고 바로 기동한다. 트레이 런처가 쓴다.
    [switch]$RunOnly,
    # 바탕화면 바로가기만 다시 만들고 끝낸다.
    [switch]$Shortcut
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

[Console]::OutputEncoding = [Text.Encoding]::UTF8
$Host.UI.RawUI.WindowTitle = 'MangaLiveReader'

$Py        = Join-Path $Root '.venv\Scripts\python.exe'
$ModeFile  = Join-Path $Root '.setup-mode'
$DoneFile  = Join-Path $Root '.setup-done'
$LocalToml = Join-Path $Root 'service.local.toml'
$EnvFile   = Join-Path $env:USERPROFILE '.config\mangalivereader\env'
$IconPng   = Join-Path $Root 'extension\icons\icon128.png'
$IconIco   = Join-Path $Root 'icon.ico'
$Vbs       = Join-Path $Root 'MangaLiveReader.vbs'

# ---------------------------------------------------------------------------
# 출력
# ---------------------------------------------------------------------------
$script:StepNo = 0
$script:StepTotal = 5

function Step($text) {
    $script:StepNo++
    Write-Host ''
    Write-Host ("[{0}/{1}] {2}" -f $script:StepNo, $script:StepTotal, $text) -ForegroundColor Cyan
}
function Ok($text)   { Write-Host "      $text" -ForegroundColor DarkGray }
function Warn($text) { Write-Host "  !   $text" -ForegroundColor Yellow }

function Die($text) {
    Write-Host ''
    Write-Host "  X   $text" -ForegroundColor Red
    Write-Host ''
    Read-Host '엔터를 누르면 창이 닫힙니다'
    exit 1
}

# ---------------------------------------------------------------------------
# 1. uv — 파이썬 3.11 까지 이것이 알아서 받는다
# ---------------------------------------------------------------------------
function Find-Uv {
    $cmd = Get-Command uv -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    # 설치 직후에는 PATH 가 이 세션에 반영되지 않는다. 기본 위치를 직접 본다.
    $local = Join-Path $env:USERPROFILE '.local\bin\uv.exe'
    if (Test-Path $local) { return $local }
    return $null
}

# 값을 return 하지 않고 여기에 담는다. uv 설치 스크립트가 출력 스트림에 무엇을
# 흘릴지 알 수 없는데, PowerShell 함수는 그것까지 통째로 반환값에 실어 준다.
$script:UvPath = $null

function Install-Uv {
    Ok '없음 → 설치합니다 (약 30MB)'
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    try {
        Invoke-Expression (Invoke-RestMethod -Uri 'https://astral.sh/uv/install.ps1')
    } catch {
        Die @"
uv 설치에 실패했습니다. 인터넷 연결을 확인하고 다시 실행해 주세요.
직접 설치하려면: https://docs.astral.sh/uv/getting-started/installation/
  ($($_.Exception.Message))
"@
    }
    $env:Path = "$env:USERPROFILE\.local\bin;$env:Path"
    $script:UvPath = Find-Uv
    if (-not $script:UvPath) { Die 'uv 를 설치했는데 찾지 못했습니다. 창을 닫고 다시 실행해 주세요.' }
}

# ---------------------------------------------------------------------------
# 2. GPU 갈래
# ---------------------------------------------------------------------------
function Get-GpuVendor {
    # nvidia-smi 가 있으면 드라이버가 깔린 것이라 가장 확실하다.
    if (Get-Command nvidia-smi -ErrorAction SilentlyContinue) { return 'nvidia' }
    try {
        $names = (Get-CimInstance Win32_VideoController -ErrorAction Stop).Name -join ' | '
    } catch {
        return 'unknown'
    }
    if ($names -match 'NVIDIA|GeForce|RTX|GTX|Quadro|Tesla') { return 'nvidia' }
    if ($names -match 'Radeon|AMD')                          { return 'amd' }
    if ($names -match 'Intel|Arc')                           { return 'intel' }
    return 'unknown'
}

function Confirm-CpuMode($vendor) {
    $label = switch ($vendor) {
        'amd'   { '라데온(AMD)' }
        'intel' { '인텔 내장 그래픽' }
        default { 'NVIDIA 가 아닌 그래픽카드' }
    }
    Write-Host ''
    Warn "$label 이 감지됐습니다."
    Write-Host ''
    Write-Host '  이 프로그램의 글자 인식은 NVIDIA GPU 를 씁니다. 그래픽카드 없이도' -ForegroundColor Yellow
    Write-Host '  돌아가긴 하지만 한 장에 5초 넘게 걸려 실사용은 어렵습니다.' -ForegroundColor Yellow
    Write-Host ''
    Write-Host '  라데온은 다른 방법이 없습니다 — ROCm 은 리눅스 전용입니다.'
    Write-Host ''
    Write-Host '    [1] 그래도 설치 (약 700MB, 느림)'
    Write-Host '    [2] 취소'
    Write-Host ''
    $ans = Read-Host '  고르세요 (1/2)'
    if ($ans -ne '1') {
        Write-Host ''
        Write-Host '  취소했습니다.' -ForegroundColor DarkGray
        Write-Host ''
        exit 0
    }
}

# ---------------------------------------------------------------------------
# 3. venv
# ---------------------------------------------------------------------------
function Assert-NotLinuxVenv {
    # WSL 셸에서 맨 `uv run` 을 치면 이 `.venv` 가 리눅스용으로 덮인다.
    # 그 상태에서 윈도우 uv sync 는 `lib64` 심볼릭 링크에서 「액세스가 거부되었습니다」
    # 로 멈춘다 — WSL 이 만든 리파스 포인트라 윈도우가 지우지 못한다.
    $cfg = Join-Path $Root '.venv\pyvenv.cfg'
    if (-not (Test-Path $cfg)) { return }
    if ((Get-Content $cfg -Raw) -notmatch 'linux') { return }
    Die @"
`.venv` 가 리눅스용입니다. WSL 셸에서 `uv run`/`uv sync` 를 친 흔적입니다.
WSL 에서 먼저 지우고 이 창을 다시 실행해 주세요:

  rm -rf '$($Root -replace '\\','/' -replace '^C:','/mnt/c')/.venv'
"@
}

function Sync-Venv($uv, $mode) {
    # `$args` 는 PowerShell 의 자동 변수라 이름을 피한다.
    $syncArgs = @('sync', '--python', '3.11', '--extra', 'launcher')
    if ($mode -eq 'cpu') {
        # torch 계열만 빼고 나머지를 깐다. 빼지 않으면 pyproject 의 cu128
        # 인덱스가 걸려 쓰지도 못할 NVIDIA 런타임 4.2GB 를 받는다.
        $syncArgs += @('--no-install-package', 'torch', '--no-install-package', 'torchvision')
    }
    & $uv @syncArgs
    if ($LASTEXITCODE -ne 0) { Die "의존성 설치에 실패했습니다 (uv sync, 종료 코드 $LASTEXITCODE)." }

    if ($mode -eq 'cpu') {
        Ok 'CPU 판 torch 를 받습니다'
        & $uv pip install --python $Py --index-url 'https://download.pytorch.org/whl/cpu' torch torchvision
        if ($LASTEXITCODE -ne 0) { Die "CPU 판 torch 설치에 실패했습니다 (종료 코드 $LASTEXITCODE)." }
    }
}

function Set-CpuDevice {
    # service.toml 의 `[models].device = "cuda"` 를 덮는다. 이 파일은 gitignore
    # 되어 있어 배포 zip 에는 없다 — 받는 사람 기계에서 처음 생긴다.
    if (Test-Path $LocalToml) {
        if ((Get-Content $LocalToml -Raw) -notmatch 'device') {
            Warn "service.local.toml 이 이미 있습니다. [models] device = `"cpu`" 를 직접 넣어 주세요."
        }
        return
    }
    $body = @'
# 이 기계의 값. 커밋되지 않는다 (.gitignore).
# NVIDIA GPU 가 없어 CPU 로 돈다. 런처가 만들었다.

[models]
device = "cpu"
'@
    [IO.File]::WriteAllText($LocalToml, $body, (New-Object Text.UTF8Encoding $false))
    Ok 'service.local.toml 에 device = "cpu" 를 적었습니다'
}

# ---------------------------------------------------------------------------
# 4. API 키
# ---------------------------------------------------------------------------
function Test-HasKey {
    if ($env:ANTHROPIC_API_KEY -or $env:GEMINI_API_KEY) { return $true }
    if (Test-Path $EnvFile) { return $true }
    if (Test-Path (Join-Path $Root '.env.local')) { return $true }
    return $false
}

function Request-ApiKey {
    Write-Host ''
    Write-Host '  번역에 쓸 API 키가 필요합니다. 둘 중 아무거나 하나:' -ForegroundColor Yellow
    Write-Host ''
    Write-Host '    Anthropic  https://console.anthropic.com/settings/keys   (sk-ant-... 로 시작)'
    Write-Host '    Gemini     https://aistudio.google.com/apikey            (AIza... 로 시작)'
    Write-Host ''
    Write-Host '  200쪽 한 권에 $1.6~2.4 정도 듭니다. 각자 자기 키를 씁니다.'
    Write-Host '  그냥 엔터를 치면 번역 없이 글자 인식만 돌아갑니다.'
    Write-Host ''
    $key = (Read-Host '  키를 붙여넣으세요').Trim()

    if (-not $key) {
        Warn '키 없이 진행합니다. 원문만 보이고 번역은 안 됩니다.'
        return
    }

    $name = if ($key.StartsWith('sk-ant-')) { 'ANTHROPIC_API_KEY' }
            elseif ($key.StartsWith('AIza')) { 'GEMINI_API_KEY' }
            else { $null }

    if (-not $name) {
        Write-Host ''
        Write-Host '    [1] Anthropic 키다'
        Write-Host '    [2] Gemini 키다'
        Write-Host ''
        $name = if ((Read-Host '  어느 쪽인가요 (1/2)') -eq '2') { 'GEMINI_API_KEY' } else { 'ANTHROPIC_API_KEY' }
    }

    $dir = Split-Path -Parent $EnvFile
    if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
    # 사용자 프로필 이하라 ACL 로 다른 계정과 격리된다 (src/mtl_service/env.py).
    [IO.File]::WriteAllText($EnvFile, "$name=$key`n", (New-Object Text.UTF8Encoding $false))
    Ok "저장했습니다 → $EnvFile"
}

# ---------------------------------------------------------------------------
# 5. 첫 실행 안내 — 확장은 런처가 대신 깔아 줄 수 없다
# ---------------------------------------------------------------------------
function Show-ExtensionGuide {
    if (Test-Path $DoneFile) { return }
    Write-Host ''
    Write-Host '  ─────────────────────────────────────────────────────────────' -ForegroundColor DarkGray
    Write-Host '  크롬 확장을 한 번만 등록해 주세요 (웹스토어에 없습니다)' -ForegroundColor Cyan
    Write-Host ''
    Write-Host '    1. 크롬 주소창에  chrome://extensions'
    Write-Host '    2. 오른쪽 위 「개발자 모드」 켜기'
    Write-Host '    3. 「압축해제된 확장 프로그램을 로드」 누르고 이 폴더 고르기:'
    Write-Host ''
    Write-Host "         $Root\extension" -ForegroundColor White
    Write-Host ''
    Write-Host '    4. 만화 페이지에서  Alt+Shift+M'
    Write-Host '  ─────────────────────────────────────────────────────────────' -ForegroundColor DarkGray
    Write-Host ''
    Read-Host '  다 하셨으면 엔터 (다음부터는 안 물어봅니다)' | Out-Null
    New-Item -ItemType File -Path $DoneFile -Force | Out-Null
}

# ---------------------------------------------------------------------------
# 6. 바탕화면 바로가기
#
# wscript.exe 로 .vbs 를 부른다 — 그래야 콘솔 창이 안 뜨고 트레이로 바로 간다.
# .vbs 를 직접 TargetPath 로 잡으면 아이콘이 스크립트 기본 아이콘으로 나온다.
# ---------------------------------------------------------------------------
function New-DesktopShortcut {
    if (-not (Test-Path $IconIco)) {
        & $Py -X utf8 -c @"
from PIL import Image
Image.open(r'$IconPng').save(r'$IconIco', sizes=[(16,16),(32,32),(48,48),(128,128)])
"@
    }
    $lnk = Join-Path ([Environment]::GetFolderPath('Desktop')) 'MangaLiveReader.lnk'
    $wsh = New-Object -ComObject WScript.Shell
    $sc = $wsh.CreateShortcut($lnk)
    $sc.TargetPath = Join-Path $env:SystemRoot 'System32\wscript.exe'
    $sc.Arguments = "`"$Vbs`""
    $sc.WorkingDirectory = $Root
    $sc.Description = 'MangaLiveReader — 만화 번역 서비스'
    if (Test-Path $IconIco) { $sc.IconLocation = "$IconIco,0" }
    $sc.Save()
    Ok "바탕화면에 만들었습니다 → $lnk"
}

function Offer-Shortcut {
    Write-Host ''
    $ans = Read-Host '  바탕화면에 바로가기를 만들까요? (Y/n)'
    if ($ans -and $ans.Trim().ToLower().StartsWith('n')) { return }
    try {
        New-DesktopShortcut
        Write-Host ''
        Write-Host '  다음부터는 그 아이콘을 누르세요. 창 없이 트레이로 뜹니다.' -ForegroundColor DarkGray
    } catch {
        Warn "바로가기를 만들지 못했습니다: $($_.Exception.Message)"
    }
}

# ---------------------------------------------------------------------------
# 본체
# ---------------------------------------------------------------------------
if ($Shortcut) {
    if (-not (Test-Path $Py)) { Die '아직 설치되지 않았습니다. MangaLiveReader.cmd 를 먼저 실행하세요.' }
    New-DesktopShortcut
    exit 0
}

if ($RunOnly -and (Test-Path $Py)) {
    & $Py -X utf8 -m mtl_service
    exit $LASTEXITCODE
}

Write-Host ''
Write-Host '  MangaLiveReader' -ForegroundColor White
Write-Host '  ───────────────' -ForegroundColor DarkGray

$firstRun = -not (Test-Path $Py)
if ($firstRun) {
    Write-Host ''
    Write-Host '  처음 실행이라 필요한 것을 받습니다. 십몇 분 걸리고, 다음부터는' -ForegroundColor DarkGray
    Write-Host '  몇 초 만에 뜹니다. 이 창은 켜 둔 채로 두세요.' -ForegroundColor DarkGray
}

Step 'uv 확인'
$uv = Find-Uv
if ($uv) {
    Ok "있음 ($uv)"
} else {
    Install-Uv
    $uv = $script:UvPath
}

Step '그래픽카드 확인'
if (Test-Path $ModeFile) {
    $mode = (Get-Content $ModeFile -Raw).Trim()
    Ok "지난번 설정 그대로 ($mode)"
} else {
    $vendor = Get-GpuVendor
    if ($vendor -eq 'nvidia') {
        $mode = 'cuda'
        Ok 'NVIDIA — GPU 로 돌립니다'
    } else {
        Confirm-CpuMode $vendor
        $mode = 'cpu'
    }
}

Step '파이썬 환경'
Assert-NotLinuxVenv
if ($firstRun) {
    # 실측: 윈도우 cu128 venv 4.8GB, CPU 판은 torch 만 갈아끼우니 훨씬 작다.
    $size = if ($mode -eq 'cuda') { '약 5GB' } else { '약 700MB' }
    Ok "받는 중입니다 ($size). 진행 표시가 멈춘 것처럼 보여도 기다려 주세요."
}
Sync-Venv $uv $mode
if (-not (Test-Path $Py)) { Die '파이썬 환경을 만들지 못했습니다.' }
if ($mode -eq 'cpu') { Set-CpuDevice }
[IO.File]::WriteAllText($ModeFile, "$mode`n", (New-Object Text.UTF8Encoding $false))
Ok '준비됨'

Step '모델 가중치'
& $Py -X utf8 (Join-Path $Root 'scripts\fetch_models.py')
if ($LASTEXITCODE -ne 0) { Die "모델을 받지 못했습니다 (종료 코드 $LASTEXITCODE)." }

Step 'API 키'
if (Test-HasKey) { Ok '있음' } else { Request-ApiKey }

$needGuide = -not (Test-Path $DoneFile)
Show-ExtensionGuide
if ($needGuide) { Offer-Shortcut }

# GPU 를 실제로 잡았는지는 torch 를 깔아 봐야만 알 수 있다. 설정이 cuda 인데
# 못 잡으면 조용히 CPU 로 떨어져 10배 느려지므로 기동 전에 말해 준다.
#
# **NVIDIA 라고 다 되는 것이 아니다.** cu128 빌드가 담고 있는 아키는
# sm_75(튜링, RTX 20/GTX 16) 이상이라 GTX 10 시리즈(sm_61)는 커널이 없어
# `no kernel image is available` 로 죽는다. 기본 PyPI 휠(CUDA 13)도 파스칼을
# 뺐으니 인덱스를 바꿔도 마찬가지다. 이름만 보고는 못 가르니 여기서 대조한다.
Write-Host ''
& $Py -X utf8 -c @"
import torch
if not torch.cuda.is_available():
    print(f'GPU 없이 CPU 로 돕니다 · torch {torch.__version__}')
    raise SystemExit(0)
name = torch.cuda.get_device_name(0)
major, minor = torch.cuda.get_device_capability(0)
arch = f'sm_{major}{minor}'
if arch in torch.cuda.get_arch_list():
    print(f'GPU: {name} · torch {torch.__version__}')
else:
    print(f'! {name} ({arch}) 는 이 torch 빌드가 지원하지 않습니다.')
    print('  GTX 10 시리즈 이하가 여기 걸립니다. CPU 로 돌리려면 창을 닫고')
    print('  .setup-mode 파일을 지운 뒤 다시 실행하세요 (느립니다).')
"@

& $Py -X utf8 -m mtl_service
$code = $LASTEXITCODE

Write-Host ''
if ($code -ne 0) {
    Write-Host "  서비스가 종료됐습니다 (코드 $code)." -ForegroundColor Yellow
} else {
    Write-Host '  서비스가 종료됐습니다.' -ForegroundColor DarkGray
}
Read-Host '  엔터를 누르면 창이 닫힙니다' | Out-Null
