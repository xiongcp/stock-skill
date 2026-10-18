# stock-skill 一键安装脚本（Windows PowerShell 5.1+ / PowerShell 7）
#
#   irm https://raw.githubusercontent.com/xiongcp/stock-skill/main/install.ps1 | iex
#
# 带参数运行：
#   & ([scriptblock]::Create((irm https://raw.githubusercontent.com/xiongcp/stock-skill/main/install.ps1))) -Agents claude,codex -DryRun
#
# 与 install.sh 流程一致。创建软链（目录符号链接）需要开发者模式或管理员权限；失败时自动改为复制。
# 注意：本脚本尚未在 Windows 上实测，遇到问题请按 README 手动安装并反馈。
[CmdletBinding()]
param(
    [string]$Dir = $(if ($env:STOCK_SKILL_DIR) { $env:STOCK_SKILL_DIR } else { [IO.Path]::Combine($HOME, '.local', 'share', 'stock-skill') }),
    [string]$Source = 'https://github.com/xiongcp/stock-skill.git',
    [string[]]$Agents = @(),
    [string]$Project = '',
    [switch]$Copy,
    [switch]$InstallUv,
    [switch]$NoTest,
    [switch]$DryRun,
    [switch]$Update,
    [switch]$Uninstall
)

$ErrorActionPreference = 'Stop'
$SkillName = 'kline-structure-chart'
$MarkerName = '.stock-skill-installer'
$AllAgents = @('claude', 'codex', 'gemini', 'cursor', 'opencode')

function Say([string]$Msg) { Write-Host "==> $Msg" -ForegroundColor Cyan }
function Warn([string]$Msg) { Write-Warning $Msg }
function Die([string]$Msg) { Write-Host "[错误] $Msg" -ForegroundColor Red; exit 1 }

# 打印并执行；-DryRun 时只打印
function Invoke-Step([string]$Desc, [scriptblock]$Action) {
    Write-Host "    + $Desc"
    if (-not $DryRun) { & $Action }
}

function Invoke-Native([string]$Exe, [string[]]$Arguments) {
    Write-Host "    + $Exe $($Arguments -join ' ')"
    if (-not $DryRun) {
        & $Exe @Arguments
        if ($LASTEXITCODE -ne 0) { Die "$Exe 执行失败（退出码 $LASTEXITCODE）" }
    }
}

$Dir = [System.IO.Path]::GetFullPath($Dir)
$SkillSrc = Join-Path $Dir $SkillName
if ($Project) {
    if (-not (Test-Path -LiteralPath $Project -PathType Container)) { Die "项目目录不存在: $Project" }
    $Project = (Resolve-Path -LiteralPath $Project).Path
}
if (Test-Path -LiteralPath $Source -PathType Container) { $Source = (Resolve-Path -LiteralPath $Source).Path }

function Get-SkillsDir([string]$Agent) {
    if ($Project) {
        if ($Agent -eq 'claude') { return [IO.Path]::Combine($Project, '.claude', 'skills') }
        return [IO.Path]::Combine($Project, '.agents', 'skills')
    }
    switch ($Agent) {
        'claude' { return [IO.Path]::Combine($HOME, '.claude', 'skills') }
        'codex' { return [IO.Path]::Combine($HOME, '.agents', 'skills') }
        'gemini' { return [IO.Path]::Combine($HOME, '.gemini', 'skills') }
        'cursor' { return [IO.Path]::Combine($HOME, '.cursor', 'skills') }
        'opencode' { return [IO.Path]::Combine($HOME, '.config', 'opencode', 'skills') }
        default { Die "不支持的 agent: $Agent（可选 $($AllAgents -join ', ')）" }
    }
}

function Resolve-Agents {
    $list = @($Agents | ForEach-Object { $_ -split ',' } | Where-Object { $_ })
    if ($list.Count -eq 1 -and $list[0] -eq 'all') { return $AllAgents }
    if ($list.Count -gt 0) { return $list }
    $found = @()
    $commands = @{ claude = 'claude'; codex = 'codex'; gemini = 'gemini'; cursor = 'cursor-agent'; opencode = 'opencode' }
    foreach ($a in $AllAgents) {
        if (Get-Command $commands[$a] -ErrorAction SilentlyContinue) { $found += $a }
    }
    if ($found.Count -eq 0) {
        Warn '未探测到 claude / codex / gemini / cursor-agent / opencode 命令，默认注册到 Claude Code 与 ~/.agents/skills'
        $found = @('claude', 'codex')
    }
    return $found
}

function Get-UniqueSkillDirs([string[]]$AgentList) {
    $AgentList | ForEach-Object { Get-SkillsDir $_ } | Select-Object -Unique
}

function Test-IsOurs([string]$Target) {
    $item = Get-Item -LiteralPath $Target -Force -ErrorAction SilentlyContinue
    if (-not $item) { return $false }
    if ($item.LinkType) { return ([string]$item.Target -eq $SkillSrc) -or (@($item.Target) -contains $SkillSrc) }
    return (Test-Path -LiteralPath (Join-Path $Target $MarkerName))
}

function Remove-Entry([string]$Target) {
    $item = Get-Item -LiteralPath $Target -Force
    # 目录链接只删除链接本身，不递归删除目标内容
    if ($item.LinkType) { $item.Delete() } else { Remove-Item -LiteralPath $Target -Recurse -Force }
}

function Copy-Skill([string]$Target) {
    New-Item -ItemType Directory -Path $Target -Force | Out-Null
    $exclude = @('.venv', 'artifacts', '__pycache__', '.pytest_cache')
    Get-ChildItem -LiteralPath $SkillSrc -Force | Where-Object { $exclude -notcontains $_.Name } |
        ForEach-Object { Copy-Item -LiteralPath $_.FullName -Destination $Target -Recurse -Force }
    Set-Content -LiteralPath (Join-Path $Target $MarkerName) -Value "installed-by=install.ps1`nsource=$SkillSrc" -Encoding UTF8
}

function Register-One([string]$SkillsDir) {
    $target = Join-Path $SkillsDir $SkillName
    Invoke-Step "New-Item -ItemType Directory $SkillsDir" { New-Item -ItemType Directory -Path $SkillsDir -Force | Out-Null }
    $existing = Get-Item -LiteralPath $target -Force -ErrorAction SilentlyContinue
    if ($existing) {
        if ($existing.LinkType -or (Test-IsOurs $target)) {
            Invoke-Step "删除旧入口 $target" { Remove-Entry $target }
        } else {
            $backup = "$target.bak.$(Get-Date -Format yyyyMMddHHmmss)"
            Warn "$target 已存在且不是链接，先备份到 $backup"
            Invoke-Step "Move-Item $target $backup" { Move-Item -LiteralPath $target -Destination $backup }
        }
    }
    if ($Copy) {
        Invoke-Step "复制 $SkillSrc -> $target（排除 .venv / artifacts）" { Copy-Skill $target }
        return
    }
    Write-Host "    + New-Item -ItemType SymbolicLink $target -> $SkillSrc"
    if ($DryRun) { return }
    try {
        New-Item -ItemType SymbolicLink -Path $target -Target $SkillSrc -ErrorAction Stop | Out-Null
    } catch {
        Warn "创建软链失败（需要开发者模式或管理员权限）：$($_.Exception.Message)；改为复制。更新代码后请重新执行本脚本。"
        Copy-Skill $target
    }
}

function Install-Uv {
    if (Get-Command uv -ErrorAction SilentlyContinue) { Say "uv 已安装：$(uv --version)"; return }
    if (-not $InstallUv) {
        $interactive = [Environment]::UserInteractive -and -not [Console]::IsInputRedirected
        if ($interactive) {
            $ans = Read-Host '未找到 uv。是否用官方安装器安装（irm https://astral.sh/uv/install.ps1 | iex）？[y/N]'
            if ($ans -match '^(y|yes)$') { $script:InstallUv = $true }
        }
        if (-not $InstallUv) { Die '需要 uv：请先安装（https://docs.astral.sh/uv/），或加 -InstallUv 让本脚本安装' }
    }
    Say '安装 uv'
    Write-Host '    + irm https://astral.sh/uv/install.ps1 | iex'
    if (-not $DryRun) {
        Invoke-RestMethod https://astral.sh/uv/install.ps1 | Invoke-Expression
        $env:Path = "$([IO.Path]::Combine($HOME, '.local', 'bin'))$([IO.Path]::PathSeparator)$env:Path"
        if (-not (Get-Command uv -ErrorAction SilentlyContinue)) { Die 'uv 安装后仍不在 PATH 中，请新开 PowerShell 后重试' }
    }
}

function Invoke-Uninstall {
    Say "卸载 $SkillName（只删除本脚本创建的内容）"
    $agentList = if ($Agents.Count -gt 0) { Resolve-Agents } else { $AllAgents }
    $removed = 0
    foreach ($d in (Get-UniqueSkillDirs $agentList)) {
        $target = Join-Path $d $SkillName
        if (Get-Item -LiteralPath $target -Force -ErrorAction SilentlyContinue) {
            if (Test-IsOurs $target) {
                Invoke-Step "删除 $target" { Remove-Entry $target }
                $removed++
            } else {
                Warn "跳过 ${target}：不是本脚本创建的（未删除）"
            }
        }
        Get-ChildItem -LiteralPath $d -Filter "$SkillName.bak.*" -Force -ErrorAction SilentlyContinue |
            ForEach-Object { Warn "保留了安装时的备份 $($_.FullName)，如不需要请手动删除" }
    }
    if (Test-Path -LiteralPath $Dir) {
        if (Test-Path -LiteralPath ([IO.Path]::Combine($Dir, '.git', $MarkerName))) {
            Invoke-Step "删除 $Dir" { Remove-Item -LiteralPath $Dir -Recurse -Force }
        } else {
            Warn "跳过 ${Dir}：不是本脚本克隆的代码目录（未删除）"
        }
    }
    Say "卸载完成，删除技能入口 $removed 个"
}

function Invoke-Install {
    if ($DryRun) { Say '演练模式（-DryRun）：只打印，不修改任何文件' }
    if (-not (Get-Command git -ErrorAction SilentlyContinue)) { Die '需要 git：https://git-scm.com/download/win' }
    Say "git 已安装：$(git --version)"
    Install-Uv

    if (Test-Path -LiteralPath (Join-Path $Dir '.git')) {
        Say "更新代码：$Dir"
        Invoke-Native git @('-C', $Dir, 'pull', '--ff-only')
    } elseif ((Test-Path -LiteralPath $Dir) -and (Get-ChildItem -LiteralPath $Dir -Force | Select-Object -First 1)) {
        Die "$Dir 已存在且不是 Git 仓库；请换一个 -Dir 或手动处理"
    } else {
        Say "克隆代码：$Source -> $Dir"
        Invoke-Native git @('clone', '--depth', '1', $Source, $Dir)
        if (-not $DryRun) {
            Set-Content -LiteralPath ([IO.Path]::Combine($Dir, '.git', $MarkerName)) -Value "installed-by=install.ps1`nsource=$Source" -Encoding UTF8
        }
    }
    if (-not $DryRun -and -not (Test-Path -LiteralPath (Join-Path $SkillSrc 'SKILL.md'))) { Die "$(Join-Path $SkillSrc 'SKILL.md') 不存在，代码目录不完整" }

    Say '安装 Python 3.12 与锁定依赖（uv.lock）'
    Invoke-Native uv @('sync', '--frozen', '--python', '3.12', '--directory', $SkillSrc)

    $agentList = Resolve-Agents
    Say "注册到 agent：$($agentList -join ', ')"
    foreach ($d in (Get-UniqueSkillDirs $agentList)) { Register-One $d }

    if ($NoTest) {
        Say '跳过自检（-NoTest）'
    } else {
        Say '离线快速自检（文档契约 + 工具测试，不访问网络）'
        Invoke-Native uv @('run', '--frozen', '--directory', $SkillSrc, 'pytest', '-q', 'tests/test_docs_contract.py', 'tests/test_tools.py')
        Say '环境自检 doctor（数据源连通项需要联网，失败不影响安装）'
        $doctorArgs = @('run', '--frozen', '--directory', $SkillSrc, 'python', 'scripts/doctor.py')
        if ($Project) { $doctorArgs += @('--project', $Project) }
        Write-Host "    + uv $($doctorArgs -join ' ')"
        if (-not $DryRun) {
            & uv @doctorArgs
            if ($LASTEXITCODE -ne 0) { Warn 'doctor 有未通过项，请按上面的建议处理' }
        }
    }

    Write-Host ''
    Write-Host '安装完成。'
    Write-Host "  代码目录：$Dir"
    Write-Host '  更新：    重新执行同一条安装命令（或加 -Update）'
    Write-Host "  卸载：    powershell -ExecutionPolicy Bypass -File `"$(Join-Path $Dir 'install.ps1')`" -Uninstall"
    Write-Host ''
    Write-Host '下一步：新开一个 Claude Code / Codex / Gemini CLI 会话，试问"帮我分析 600519.SH"或"看看茅台的 K 线"。'
}

if ($Uninstall) { Invoke-Uninstall } else { Invoke-Install }
