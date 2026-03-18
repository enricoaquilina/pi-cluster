name: feature-dev
description: Deterministic feature development pipeline — plan → setup → develop → verify → test → review
args:
  feature: { default: "" }
  repo: { default: "/opt/workspace" }
steps:
  - id: plan
    openclaw.invoke:
      agent: feature-dev_planner
      message: "Plan the implementation for this feature in $repo: $feature"

  - id: setup
    openclaw.invoke:
      agent: feature-dev_setup
      message: "Set up the development environment based on plan: $plan.stdout"
      stdin: $plan.stdout

  - id: develop
    openclaw.invoke:
      agent: feature-dev_developer
      message: "Implement the feature based on plan: $plan.stdout"
      stdin: $plan.stdout
    approval: required

  - id: verify
    openclaw.invoke:
      agent: feature-dev_verifier
      message: "Verify the implementation: $develop.stdout"
      stdin: $develop.stdout

  - id: test
    openclaw.invoke:
      agent: feature-dev_tester
      message: "Run tests for the implementation: $develop.stdout"
      stdin: $develop.stdout

  - id: review
    openclaw.invoke:
      agent: feature-dev_reviewer
      message: "Review the implementation. Verification: $verify.stdout. Tests: $test.stdout"
      stdin: $verify.stdout
