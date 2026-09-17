param(
  [string]$Python = "python",
  [string]$VenvDir = ".venv",
  [switch]$NoVenv,
  [switch]$SkipSmoke
)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

function Ensure-Venv {
  if ($NoVenv) { return }
  if (-not (Test-Path $VenvDir)) {
    & $Python -m venv $VenvDir
  }
  $venvPython = Join-Path $VenvDir "Scripts\python.exe"
  if (-not (Test-Path $venvPython)) {
    throw "Venv create failed: $venvPython missing"
  }
  $script:Python = $venvPython
}

# 冻结版 GUI 程序无控制台输出，冒烟校验 = 退出码 0 + 日志含 SMOKE_TEST_OK 标记
function Invoke-FrozenSmoke([string]$ExePath) {
  Write-Host "== 冒烟测试: $ExePath"
  $p = Start-Process -FilePath $ExePath -ArgumentList "--smoke-test" -Wait -PassThru
  $code = $p.ExitCode
  $logFile = Join-Path (Split-Path $ExePath) "user_data\logs\smoke_test.log"
  $logOk = (Test-Path $logFile) -and
           (Select-String -Path $logFile -Pattern "SMOKE_TEST_OK" -Quiet)
  if (($code -ne 0) -or (-not $logOk)) {
    throw "冻结冒烟测试失败: $ExePath (exit=$code, logOK=$logOk)"
  }
  Write-Host "== 冒烟通过: $ExePath"
}

Push-Location $PSScriptRoot
try {
  Ensure-Venv

  Write-Host "== 安装依赖（运行 + 构建）"
  & $Python -m pip install -U pip
  & $Python -m pip install -r requirements.txt -r requirements-build.txt

  # 1) 开发环境冒烟（验证依赖安装正确）
  if (-not $SkipSmoke) {
    & $Python main.py --smoke-test
    if ($LASTEXITCODE -ne 0) { throw "开发环境冒烟测试失败" }
  }

  # 2) 清理旧产物
  Remove-Item -Recurse -Force "dist", "build" -ErrorAction SilentlyContinue

  # 3) 打包（spec 一次产出 onedir 与 onefile 两种形态）
  Write-Host "== PyInstaller 构建中（约数分钟）..."
  & $Python -m PyInstaller --noconfirm --clean "ChecklistTool.spec"

  # 4) 冻结版冒烟
  if (-not $SkipSmoke) {
    Invoke-FrozenSmoke "$PSScriptRoot\dist\ChecklistTool\ChecklistTool.exe"
    Invoke-FrozenSmoke "$PSScriptRoot\dist\ChecklistTool.exe"
  }

  # 5) 清除冒烟产生的运行时数据，保证分发产物干净
  Remove-Item -Recurse -Force `
    "$PSScriptRoot\dist\ChecklistTool\user_data", "$PSScriptRoot\dist\user_data" `
    -ErrorAction SilentlyContinue
  # 可选：随包说明文件
  if (Test-Path "$PSScriptRoot\assets\使用说明.txt") {
    Copy-Item "$PSScriptRoot\assets\使用说明.txt" "$PSScriptRoot\dist\ChecklistTool\"
  }

  # 6) 压缩 onedir 为免安装 zip
  $zip = "$PSScriptRoot\dist\ChecklistTool_v2.2_免安装.zip"
  Remove-Item -Force $zip -ErrorAction SilentlyContinue
  Write-Host "== 压缩免安装包..."
  Compress-Archive -Path "$PSScriptRoot\dist\ChecklistTool" -DestinationPath $zip -CompressionLevel Optimal
  if (-not (Test-Path $zip)) {
    if (Get-Command 7z -ErrorAction SilentlyContinue) {
      & 7z a -tzip -mx9 $zip "$PSScriptRoot\dist\ChecklistTool"
      if ($LASTEXITCODE -ne 0) { throw "zip 打包失败" }
    } else {
      throw "zip 打包失败（Compress-Archive 与 7z 均未成功）"
    }
  }

  # 7) 结果汇总
  $folderSize = (Get-ChildItem "$PSScriptRoot\dist\ChecklistTool" -Recurse | Measure-Object Length -Sum).Sum
  Write-Host ""
  Write-Host "==== 构建完成 ===="
  Write-Host ("onedir  : dist\ChecklistTool\  (共 {0:N1} MB)" -f ($folderSize / 1MB))
  Write-Host ("onefile : dist\ChecklistTool.exe  ({0:N1} MB)" -f ((Get-Item "$PSScriptRoot\dist\ChecklistTool.exe").Length / 1MB))
  Write-Host ("zip     : dist\ChecklistTool_v2.2_免安装.zip  ({0:N1} MB)" -f ((Get-Item $zip).Length / 1MB))
} finally {
  Pop-Location
}
