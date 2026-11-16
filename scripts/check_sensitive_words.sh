#!/usr/bin/env bash
# Sensitive-word guard for staged changes.
# Used both by the pre-commit hook and by CI.
#
# Exit codes:
#   0  no leaks
#   1  leaks found (details printed)
#   2  bad usage
#
# Scope:
#   - by default scans STAGED files (pre-commit mode)
#   - pass --all to scan the entire working tree (CI mode)
#   - pass --self-test to check the patterns against the built-in sample
#     lines (SELF_TEST_CASES below); scans no files
#
# Environment overrides:
#   DHANDS_SENSITIVE_EXTRA  = colon-separated extra patterns to check (rare)

set -euo pipefail

MODE="staged"
if [[ "${1:-}" == "--all" ]]; then
  MODE="all"
  shift
elif [[ "${1:-}" == "--self-test" ]]; then
  MODE="selftest"
  shift
fi

# Pattern list. Each row = ERE regex. Keep case-insensitive — add (?i) inlined as -i flag below.
PATTERNS=(
  # Company / brand
  'vendor(sz)?(\.com)?'
  'com\.vendor'
  # SoC / vendor-customer specific
  'arm-soc'
  'soc[-_ ]?sdk'
  'soc[-_ ]sdk'
  # Short internal handle (word-bounded so it doesn't match the public
  # github handle skyzhangbinghua which is a legitimate attribution string)
  '\bdev\b'
  # Home-dir leaks
  '~'
  '/home/[a-z][a-z0-9_-]*/(android-llm-bridge|adb-via-|\.claude)'
  # A checkout of this repository (current or former name) under someone's
  # home, directly or below up to three parent folders such as
  # ~/claude-tools/ or ~/code/github/. Linux and macOS home roots.
  '/(home|Users)/[a-z][a-z0-9_.-]*/([A-Za-z0-9._-]+/){0,3}(android-llm-bridge|devicehands)([^A-Za-z0-9._-]|$)'
  # Internal RFC1918 IPs belonging to known private networks
  '192\.0\.2\.[0-9]{1,3}'
  '198\.51\.100\.[0-9]{1,3}'
)

if [[ -n "${DHANDS_SENSITIVE_EXTRA:-}" ]]; then
  IFS=':' read -r -a EXTRA <<< "$DHANDS_SENSITIVE_EXTRA"
  PATTERNS+=("${EXTRA[@]}")
fi

# --self-test: each case is "hit|<line>" (some pattern must match) or
# "miss|<line>" (no pattern may match). Reverse cases matter as much as the
# hits: a pattern that matches everything passes every "hit" case.
SELF_TEST_CASES=(
  'hit|/home/someone/android-llm-bridge/src/x.py'
  'hit|/home/someone/devicehands'
  'hit|/home/someone/claude-tools/devicehands/README.md'
  'hit|cd /Users/someone/code/github/devicehands && make'
  'miss|~/claude-tools/devicehands/README.md'
  'miss|/opt/tools/devicehands/bin/dhands'
  'miss|docs/devicehands-design.md'
  'miss|/home/someone/projects/devicehandsx/README.md'
)

if [[ "$MODE" == "selftest" ]]; then
  passed=0
  total=${#SELF_TEST_CASES[@]}
  for case in "${SELF_TEST_CASES[@]}"; do
    want="${case%%|*}"
    line="${case#*|}"
    got="miss"
    for pat in "${PATTERNS[@]}"; do
      if printf '%s\n' "$line" | grep -qEi -- "$pat"; then got="hit"; break; fi
    done
    if [[ "$got" == "$want" ]]; then
      passed=$((passed + 1))
    else
      echo "FAIL want=$want got=$got: $line"
    fi
  done
  echo "self-test: $passed/$total passed"
  [[ $passed -eq $total ]] && exit 0 || exit 1
fi

# Files that are legitimately allowed to mention these words.
# Keep this short. Each entry is a glob applied to the path.
ALLOWED=(
  'CLAUDE.md'              # defines the rule itself
  'CONTRIBUTING.md'         # same
  'scripts/check_sensitive_words.sh'  # this script
  '.pre-commit-config.yaml'
  '.gitignore'
  # agents 团队元文档：security-and-neutrality-auditor 引用禁用词清单
  # 让 agent 知道要扫什么。这是定义规则本身，不是泄露。
  # lessons.md 之前也整文件豁免，但 lessons 是持续增长的 journal，叙事
  # 写法易把真实识别符（真邮箱 / 真路径 / 真 SoC SKU）混进操作示例中
  # 流到公网。改为不豁免，lessons 写作必须用 generic 占位符
  # （<vendor 名> / <SoC SKU> / ~/<project> / COMxx 等）。
  '.claude/agents/security-and-neutrality-auditor.md'
)

is_allowed() {
  local path="$1"
  for glob in "${ALLOWED[@]}"; do
    if [[ "$path" == $glob ]]; then return 0; fi
  done
  return 1
}

# Collect file list.
FILES=()
if [[ "$MODE" == "staged" ]]; then
  while IFS= read -r -d '' f; do FILES+=("$f"); done \
    < <(git diff --cached --name-only --diff-filter=ACMR -z)
else
  # tracked + untracked (minus ignored) — "--all" must see brand-new files
  # too, or a pre-commit review of untracked work gets a false all-clear.
  while IFS= read -r -d '' f; do FILES+=("$f"); done \
    < <(git ls-files -z --cached --others --exclude-standard)
fi

if [[ ${#FILES[@]} -eq 0 ]]; then
  exit 0
fi

FAILED=0

scan_file() {
  local path="$1"
  if is_allowed "$path"; then return 0; fi
  [[ ! -f "$path" ]] && return 0       # deleted / moved
  # Skip binaries
  if file --mime "$path" | grep -q 'charset=binary'; then return 0; fi

  for pat in "${PATTERNS[@]}"; do
    # Case-insensitive grep (-i). Show filename + line number + offending line.
    if matches=$(grep -nEi -- "$pat" "$path" 2>/dev/null); then
      echo "---"
      echo "[sensitive] $path"
      echo "  pattern: $pat"
      echo "$matches" | sed 's/^/    /'
      FAILED=1
    fi
  done
}

# Also scan the commit message itself when run in staged mode AND a merge
# message file is pending (pre-commit's COMMIT_EDITMSG isn't available here,
# so we leave this to commit-msg hook — see .pre-commit-config.yaml).

for f in "${FILES[@]}"; do
  scan_file "$f"
done

if [[ $FAILED -eq 1 ]]; then
  echo ""
  echo "Sensitive-word guard: one or more patterns found above."
  echo "See CLAUDE.md § \"Banned words\" for the policy and generic alternatives."
  exit 1
fi

exit 0
