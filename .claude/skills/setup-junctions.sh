#!/bin/bash
# setup-junctions.sh
# Linux/macOS: 用 symlink 让 .claude/skills/<name>/ 指向 slirn/skill/<name>/
# symlink 本身不会被 git 跟踪（因为 .claude/skills/*/ 在 .gitignore 中）

set -e
cd "$(dirname "$0")/../.."

SKILLS=(
    "video-subtitle-extractor"
    "video-timestamp-cutter"
    "long-video-subtitle-cleaner"
    "course-content-review"
    "manual-review-finalizer"
    "video-subtitle-editing-pipeline"
)

created=0
skipped=0
for s in "${SKILLS[@]}"; do
    link=".claude/skills/$s"
    target="slirn/skill/$s"

    if [ ! -d "$target" ]; then
        echo "WARN: target not found: $target (submodule 未初始化？先跑 git submodule update --init)"
        continue
    fi
    if [ -e "$link" ] || [ -L "$link" ]; then
        echo "exists: $link"
        skipped=$((skipped+1))
        continue
    fi
    ln -s "../../$target" "$link"
    echo "OK: $link -> $target"
    created=$((created+1))
done

echo ""
echo "Done. Created: $created, Skipped: $skipped"
echo "Symlinks 不会被 git 跟踪（因为 .claude/skills/*/ 在 .gitignore 中）"
