# setup-junctions.ps1
# Windows: 创建 junction，让 .claude/skills/<name>/ 指向 slirn/skill/<name>/
# junction 本身不会被 git 跟踪（因为 .claude/skills/*/ 在 .gitignore 中）
# 运行后 Claude Code 在本项目里就能"看到"6 个 skill

$ErrorActionPreference = "Stop"
$root = Resolve-Path (Join-Path $PSScriptRoot "..\..")
Set-Location $root

$skills = @(
    "video-subtitle-extractor",
    "video-timestamp-cutter",
    "long-video-subtitle-cleaner",
    "course-content-review",
    "manual-review-finalizer",
    "video-subtitle-editing-pipeline"
)

$created = 0
$skipped = 0
foreach ($s in $skills) {
    $link = Join-Path ".claude\skills" $s
    $target = "slirn\skill\$s"

    if (-not (Test-Path $target)) {
        Write-Warning "Target not found: $target (submodule 未初始化？先跑 git submodule update --init)"
        continue
    }
    if (Test-Path $link) {
        Write-Output "exists: $link"
        $skipped++
        continue
    }
    New-Item -ItemType Junction -Path $link -Target $target | Out-Null
    Write-Output "OK: $link -> $target"
    $created++
}
Write-Output ""
Write-Output "Done. Created: $created, Skipped: $skipped"
Write-Output "Junctions 是 Windows-only，且 .gitignore 排除了 .claude/skills/*/"
Write-Output "如需在 Linux/macOS 上用 symlink，请用 setup-junctions.sh"
