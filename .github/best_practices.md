# Pi-Cluster Best Practices

Read by the AI code reviewer to enforce project-specific patterns.
Based on recurring issues found in PRs #77–#86.

---

## Shell Scripting

### Use if/then/else instead of &&/|| chains

**Bad:**
```bash
check_service "$node" ms && report up "$node" || report down "$node"
```

**Good:**
```bash
if check_service "$node" ms; then
  report up "$node"
else
  report down "$node"
fi
```

**Why:** If `check_service` succeeds but `report up` exits non-zero for unrelated reasons,
the `||` handler fires falsely — node reported as down when it's up. (PR #86)

---

### Use extended regex in grep

**Bad:**
```bash
grep 'health.pause' logs       # . is literal in basic regex
```

**Good:**
```bash
grep -E 'health\.pause' logs
```

---

### Anchor port patterns to avoid false matches

**Bad:**
```bash
grep "$port/tcp.*ALLOW"   # port 80 matches 8000:8010 range
```

**Good:**
```bash
grep -E "\b${port}/tcp\b.*ALLOW"
```

---

### SSH empty result handling

**Bad:**
```bash
result=$(ssh host "check something")
process "$result"   # misbehaves if host unreachable
```

**Good:**
```bash
result=$(ssh host "check something" 2>/dev/null || true)
if [[ -z "$result" ]]; then
  warn "host unreachable — skipping"
  return
fi
process "$result"
```

---

### find: use -not not !

**Bad:**
```bash
find . ! -perm -g+s
```

**Good:**
```bash
find . -not -perm -g+s
```

---

## CI Workflows

### Never hardcode credentials

**Bad:**
```yaml
POSTGRES_PASSWORD: testpassword
```

**Good (ephemeral, for test DBs on CI runners):**
```bash
TEST_PASS=$(openssl rand -hex 16)
sudo -u postgres psql -c "CREATE USER foo WITH PASSWORD '${TEST_PASS}';"
echo "TEST_DB_PASS=${TEST_PASS}" >> $GITHUB_ENV
```

---

### Pin GitHub Actions to release tags

**Bad:**
```yaml
uses: qodo-ai/pr-agent@main
```

**Good:**
```yaml
uses: qodo-ai/pr-agent@v0.32
```

---

## Python (Mission Control)

### Health endpoints must return 503 when unhealthy

**Bad:**
Returns HTTP 200 with `{"status": "degraded"}` — load balancers treat as healthy.

**Good:**
Returns HTTP 503. Caddy/nginx use the status code, not the body. (PR #82)

---

### Use specific exception types

**Bad:**
```python
except Exception:
```

**Good:**
```python
except psycopg2.Error:
```

Narrow catches are intentional in this codebase.

---

## Ansible

### find portability in tasks

**Bad:**
```yaml
command: find /path ! -perm -g+s -exec chmod g+s {} +
```

**Good:**
```yaml
command: find /path -not -perm -g+s -exec chmod g+s {} +
```
# Pipeline verification test
