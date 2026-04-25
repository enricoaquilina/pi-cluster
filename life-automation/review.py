#!/usr/bin/env python3
"""CLI for reviewing staged fact candidates in ~/life/."""
import argparse
import json
import sys

import candidates


def cmd_list(args):
    pending = candidates.pending_candidates()
    if getattr(args, "json", False):
        print(json.dumps(pending))
        return
    if not pending:
        print("No pending candidates.")
        return

    review = [c for c in pending if c.get("needs_review")]
    auto = [c for c in pending if not c.get("needs_review")]

    if review:
        print(f"\n=== Needs Review ({len(review)}) ===")
        for c in review:
            print(f"  {c['id']}  [{c['entity']}]  {c['fact'][:80]}")
            print(f"           cat={c['category']}  mentions={c.get('mentions',1)}  src={c.get('source','')}")

    if auto:
        print(f"\n=== Auto-Graduatable ({len(auto)}) ===")
        for c in auto:
            print(f"  {c['id']}  [{c['entity']}]  {c['fact'][:80]}")

    print(f"\nTotal: {len(pending)} pending ({len(review)} need review, {len(auto)} auto-graduatable)")


def cmd_graduate(args):
    result = candidates.graduate(args.candidate_id, rationale=args.rationale or "")
    if result:
        if getattr(args, "json", False):
            print(json.dumps({"status": "graduated", "id": result["id"], "entity": result["entity"], "fact": result["fact"]}))
        else:
            print(f"Graduated: {result['id']} → {result['entity']}/items.json")
        candidates.write_review_queue()
    else:
        if getattr(args, "json", False):
            print(json.dumps({"status": "error", "message": f"Not found or not pending: {args.candidate_id}"}))
        else:
            print(f"Not found or not pending: {args.candidate_id}", file=sys.stderr)
        sys.exit(1)


def cmd_reject(args):
    result = candidates.reject(args.candidate_id, rationale=args.rationale or "")
    if result:
        if getattr(args, "json", False):
            print(json.dumps({"status": "rejected", "id": result["id"], "entity": result.get("entity", "")}))
        else:
            print(f"Rejected: {result['id']}")
        candidates.write_review_queue()
    else:
        if getattr(args, "json", False):
            print(json.dumps({"status": "error", "message": f"Not found or not pending: {args.candidate_id}"}))
        else:
            print(f"Not found or not pending: {args.candidate_id}", file=sys.stderr)
        sys.exit(1)


def cmd_auto_graduate(args):
    graduatable = candidates.auto_graduatable()
    if not graduatable:
        print("No candidates qualify for auto-graduation.")
        return

    count = 0
    for c in graduatable:
        result = candidates.graduate(c["id"], rationale="auto-graduated")
        if result:
            print(f"  Graduated: {result['id']} [{result['entity']}] {result['fact'][:60]}")
            count += 1

    print(f"\nAuto-graduated {count} candidates.")
    candidates.write_review_queue()


def cmd_queue(args):
    candidates.write_review_queue()
    print(f"Review queue written to {candidates.REVIEW_QUEUE_PATH}")


def cmd_notify(args):
    """Print Telegram-formatted review summary. Exit 0 if items need review, 1 if empty."""
    review = candidates.needs_review_candidates()
    pending = candidates.pending_candidates()
    auto = [c for c in pending if not c.get("needs_review")]

    if not review and not auto:
        sys.exit(1)

    lines = ["📋 <b>Nightly Review Queue</b>", ""]

    if review:
        lines.append(f"🔍 <b>{len(review)} need human review:</b>")
        for c in review[:5]:
            cat = c.get("category", "?")
            lines.append(f"  • [{c['entity']}] {c['fact'][:60]} <i>({cat})</i>")
        if len(review) > 5:
            lines.append(f"  … and {len(review) - 5} more")
        lines.append("")

    if auto:
        lines.append(f"✅ {len(auto)} auto-graduatable (will process next run)")
        lines.append("")

    lines.append(f"Total: {len(pending)} pending")
    lines.append("Review: <code>python3 ~/life/scripts/review.py list</code>")
    print("\n".join(lines))


def main():
    parser = argparse.ArgumentParser(description="Review staged fact candidates")
    sub = parser.add_subparsers(dest="command")

    list_p = sub.add_parser("list", help="Show pending candidates")
    list_p.add_argument("--json", action="store_true", help="Output JSON")

    grad = sub.add_parser("graduate", help="Promote candidate to items.json")
    grad.add_argument("candidate_id")
    grad.add_argument("rationale", nargs="?", default="")
    grad.add_argument("--json", action="store_true", help="Output JSON")

    rej = sub.add_parser("reject", help="Reject a candidate")
    rej.add_argument("candidate_id")
    rej.add_argument("rationale", nargs="?", default="")
    rej.add_argument("--json", action="store_true", help="Output JSON")

    sub.add_parser("auto-graduate", help="Batch graduate qualifying candidates")
    sub.add_parser("queue", help="Regenerate REVIEW_QUEUE.md")
    sub.add_parser("notify", help="Print Telegram-formatted review summary")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)

    {
        "list": cmd_list,
        "graduate": cmd_graduate,
        "reject": cmd_reject,
        "auto-graduate": cmd_auto_graduate,
        "queue": cmd_queue,
        "notify": cmd_notify,
    }[args.command](args)


if __name__ == "__main__":
    main()
