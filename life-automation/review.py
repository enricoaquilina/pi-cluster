#!/usr/bin/env python3
"""CLI for reviewing staged fact candidates in ~/life/."""
import argparse
import sys

import candidates


def cmd_list(args):
    pending = candidates.pending_candidates()
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
        print(f"Graduated: {result['id']} → {result['entity']}/items.json")
        candidates.write_review_queue()
    else:
        print(f"Not found or not pending: {args.candidate_id}", file=sys.stderr)
        sys.exit(1)


def cmd_reject(args):
    result = candidates.reject(args.candidate_id, rationale=args.rationale or "")
    if result:
        print(f"Rejected: {result['id']}")
        candidates.write_review_queue()
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


def main():
    parser = argparse.ArgumentParser(description="Review staged fact candidates")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("list", help="Show pending candidates")

    grad = sub.add_parser("graduate", help="Promote candidate to items.json")
    grad.add_argument("candidate_id")
    grad.add_argument("rationale", nargs="?", default="")

    rej = sub.add_parser("reject", help="Reject a candidate")
    rej.add_argument("candidate_id")
    rej.add_argument("rationale", nargs="?", default="")

    sub.add_parser("auto-graduate", help="Batch graduate qualifying candidates")
    sub.add_parser("queue", help="Regenerate REVIEW_QUEUE.md")

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
    }[args.command](args)


if __name__ == "__main__":
    main()
