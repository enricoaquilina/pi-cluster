name: security-audit
description: Deterministic security audit pipeline — scan → prioritize → fix → verify → test → PR
args:
  target: { default: "" }
  repo: { default: "/opt/workspace" }
steps:
  - id: scan
    openclaw.invoke:
      agent: security-audit_scanner
      message: "Scan for security issues in $repo: $target"

  - id: prioritize
    openclaw.invoke:
      agent: security-audit_prioritizer
      message: "Prioritize findings by severity: $scan.stdout"
      stdin: $scan.stdout

  - id: setup
    openclaw.invoke:
      agent: security-audit_setup
      message: "Set up fix environment for prioritized issues: $prioritize.stdout"
      stdin: $prioritize.stdout

  - id: fix
    openclaw.invoke:
      agent: security-audit_fixer
      message: "Fix the highest-priority security issues: $prioritize.stdout"
      stdin: $prioritize.stdout
    approval: required

  - id: verify
    openclaw.invoke:
      agent: security-audit_verifier
      message: "Verify security fixes are correct: $fix.stdout"
      stdin: $fix.stdout

  - id: test
    openclaw.invoke:
      agent: security-audit_tester
      message: "Run security tests to confirm fixes: $fix.stdout"
      stdin: $fix.stdout

  - id: pr
    openclaw.invoke:
      agent: security-audit_pr
      message: "Create PR for verified security fixes"
      stdin: $verify.stdout
    condition: '$verify.json | jq -e ".passed"'
