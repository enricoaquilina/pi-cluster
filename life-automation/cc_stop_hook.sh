#!/bin/bash
# Claude Code Stop hook — async, may not fire in all contexts.
# The 15-min timer and nightly consolidation are safety nets.
# Must never fail — exit 0 always.
/usr/bin/python3 /home/enrico/life/scripts/cc_session_digest.py || true
/home/enrico/life/scripts/mini-consolidate.sh || true
