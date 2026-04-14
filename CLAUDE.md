DO NOT TOUCH GIT - do NOT use git writing operations like checkout, commit, merge, rebase, pull, fetch, push
DO NOT TOUCH BUILDS - do NOT run commands like make, cmake, gcc, g++, c++, ld, protobuf, protocc - or similar
DO NOT TOUCH docs/ADR.md and CLAUDE.md files - unless expclicitely agreed with user to do modifications
DO NOT TOUCH protobuf code generating for Python
DO NOT ADD code samples and line numbers to documentation as they become stale before the sunset

FEEL FREE to do READ operations within the project scope: find, ls, tree, cat, grep, awk, tr, sed or similar that needed to analyze logs or code itself
FEEL FREE to do web searches when necesscary using curl, wget or your tooling

If you need a temporary place for files use ./tmp/ inside of the project instead of "/tmp", to avoid interactive asking permissions.
