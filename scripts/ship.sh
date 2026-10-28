#!/bin/bash
# Ship a Boord Owner release: push main, cut the signed tag, push the tag.
#
# The three steps that have to happen in that order and never get skipped.
# scripts/release.sh does the signing and refuses to do it badly; this wraps
# it so a release is one command run from anywhere, rather than a cd, a push,
# a script and another push - which is four chances to stop halfway and leave
# main pushed with no tag on it, or a tag on a commit origin has never seen.
#
# It does NOT commit your work. Commit first, with whatever message the change
# deserves; this ships what is already committed.
#
# Usage:  scripts/ship.sh v1.10
#         ~/Documents/Projects/BoordOwner/scripts/ship.sh v1.10      (from anywhere)
set -euo pipefail

# Derived from this script's own location, so the working directory does not
# matter. Running `scripts/release.sh` from the wrong folder is the single
# most common way this goes wrong, and it fails with "no such file or
# directory" rather than anything that hints at the real problem.
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

TAG="${1:-}"
if [ -z "$TAG" ]; then
  echo "Usage: scripts/ship.sh <tag>     e.g. scripts/ship.sh v1.10"
  echo
  echo "Newest release tag right now: $(git tag --list 'v*' --sort=-v:refname | head -1)"
  exit 1
fi

VERSION_FILE="frontend/shared/api.js"
EXPECTED="${TAG#v}"

confirm() {
  # Reads from the terminal. If there is no terminal - run from a script, or
  # piped - refuse rather than silently taking a default, because every
  # question this asks is about something irreversible.
  local prompt="$1" reply
  if [ ! -t 0 ]; then
    echo "Error: $prompt - but there is no terminal to ask on. Run this yourself."
    exit 1
  fi
  read -r -p "$prompt [y/N] " reply
  case "$reply" in [yY]|[yY][eE][sS]) return 0 ;; *) return 1 ;; esac
}

# --- must be shipping main ---------------------------------------------------
BRANCH="$(git rev-parse --abbrev-ref HEAD)"
if [ "$BRANCH" != "main" ]; then
  echo "Error: on branch '$BRANCH', not main."
  echo "Farms track releases, but a release should be a commit that is on main -"
  echo "otherwise the tag is the only thing keeping that code alive. Merge first:"
  echo "    git checkout main && git merge --ff-only $BRANCH"
  exit 1
fi

# --- the working tree must be exactly what gets signed -----------------------
# release.sh checks this too. Checked here as well so it fails before anything
# has been pushed, rather than after main is already on origin.
if [ -n "$(git status --porcelain)" ]; then
  echo "Error: working tree is not clean. Commit or stash first - the tag signs"
  echo "a commit, so anything uncommitted is NOT in the release."
  git status --short
  exit 1
fi

if git rev-parse -q --verify "refs/tags/$TAG" >/dev/null; then
  echo "Error: tag $TAG already exists. Pick a new version - never re-point a"
  echo "released tag, or servers already on it will not re-deploy."
  exit 1
fi

# --- the in-app version must match the tag -----------------------------------
# release.sh refuses on a mismatch and tells you to fix it by hand. Since that
# is the one edit every single release needs, offer it here instead - but as
# its own commit, visible in the log, never folded into anything else.
APP_VERSION="$(sed -n 's/^  VERSION: "\(.*\)",$/\1/p' "$VERSION_FILE")"
if [ "$APP_VERSION" != "$EXPECTED" ]; then
  echo "$VERSION_FILE says VERSION \"$APP_VERSION\", but $TAG expects \"$EXPECTED\"."
  echo "Boord.VERSION is what the app's header shows, so a mismatch means"
  echo "the phone lies about what it is running."
  echo
  if ! confirm "Set it to \"$EXPECTED\" and commit that?"; then
    echo "Aborted - nothing pushed, nothing tagged."
    exit 1
  fi
  # Anchored to the same line shape release.sh greps for, so the two can never
  # disagree about what "the version" is. Written through a temp file rather
  # than sed -i: the in-place flag takes an argument on BSD sed (macOS) and
  # none on GNU sed, so the one spelling that works on both is neither.
  sed "s/^  VERSION: \"$APP_VERSION\",$/  VERSION: \"$EXPECTED\",/" "$VERSION_FILE" > "$VERSION_FILE.tmp"
  mv "$VERSION_FILE.tmp" "$VERSION_FILE"
  CHECK="$(sed -n 's/^  VERSION: "\(.*\)",$/\1/p' "$VERSION_FILE")"
  if [ "$CHECK" != "$EXPECTED" ]; then
    echo "Error: the edit did not take - $VERSION_FILE now reads \"$CHECK\"."
    echo "Put it back and set the version by hand."
    exit 1
  fi
  git add "$VERSION_FILE"
  git commit -m "chore: version $EXPECTED" >/dev/null
  echo "  committed: chore: version $EXPECTED"
  echo
fi

# --- push main before tagging ------------------------------------------------
# This order matters. A tag pushed to a commit origin has never seen leaves a
# release that exists only as a tag, and anyone looking at main cannot see the
# code that farms are running.
echo "==> Pushing main..."
git push origin main
echo

# --- sign the tag ------------------------------------------------------------
# release.sh re-runs the checks above and asks its own confirmation before it
# signs anything. It stops without pushing, on purpose.
echo "==> Cutting the signed tag..."
scripts/release.sh "$TAG"

# --- push the tag ------------------------------------------------------------
# The one step release.sh deliberately leaves out, because it is the moment a
# release becomes real to every farm. Kept as a question, not automated away -
# the fingerprint printed above is the thing to actually look at first.
echo
echo "The fingerprint above must match data\\release_key.fpr on the farm servers."
echo "If you have rotated keys, they will refuse this release until that file is"
echo "updated (README.md, Updates)."
echo
if ! confirm "Push $TAG to origin now?"; then
  echo
  echo "Not pushed. The signed tag exists locally; farms cannot see it yet."
  echo "When you are ready:  git push origin $TAG"
  exit 0
fi

git push origin "$TAG"

echo
echo "Shipped $TAG."
echo
echo "On the farm PC, in C:\\BoordOwner: double-click update_owner_server.bat."
echo "It verifies the signature, checks the tag out, brings the database up to"
echo "date (taking a copy first) and restarts the server. The owner's phone then"
echo "needs the app fully closed and reopened, not just backgrounded."
