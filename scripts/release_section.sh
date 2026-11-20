#!/usr/bin/env bash
# Print the newest release's section of a cumulative notes file
# (RELEASE_NOTES.md, client/RELEASE_NOTES.md): everything before the second
# top-level "# " heading that is not inside a code fence, trailing blank lines
# dropped. The release workflows publish it as the GitHub release body - the
# whole file made every release page carry every release ever made.
set -euo pipefail

notes="${1:?usage: release_section.sh <notes-file>}"

awk '
  /^(```|~~~)/ { fence = !fence }
  !fence && /^# / && ++headings == 2 { exit }
  /^[[:space:]]*$/ { blank++; next }
  { while (blank > 0) { print ""; blank-- } print }
' "$notes"
