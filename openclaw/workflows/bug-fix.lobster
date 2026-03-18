name: bug-fix
description: Deterministic bug-fix pipeline — triage → investigate → fix → verify → PR
args:
  issue: { default: "" }
  repo: { default: "/opt/workspace" }
steps:
  - id: triage
    openclaw.invoke:
      agent: bug-fix_triager
      message: "Triage this issue in $repo: $issue"

  - id: investigate
    openclaw.invoke:
      agent: bug-fix_investigator
      message: "Investigate based on triage: $triage.stdout"
      stdin: $triage.stdout

  - id: fix
    openclaw.invoke:
      agent: bug-fix_fixer
      message: "Fix based on investigation: $investigate.stdout"
      stdin: $investigate.stdout
    approval: required

  - id: verify
    openclaw.invoke:
      agent: bug-fix_verifier
      message: "Verify the fix: $fix.stdout"
      stdin: $fix.stdout

  - id: pr
    openclaw.invoke:
      agent: bug-fix_pr
      message: "Create PR for verified fix"
      stdin: $verify.stdout
    condition: '$verify.json | jq -e ".passed"'
