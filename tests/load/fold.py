"""Summarise a py-spy raw (folded) profile: inclusive % per function."""

import collections
import sys

counts: collections.Counter[str] = collections.Counter()
total = 0
for line in open(sys.argv[1]):
    stack, _, n = line.rstrip().rpartition(" ")
    if not n.isdigit():
        continue
    total += int(n)
    seen = set()
    for frame in stack.split(";"):
        name = frame.split(" (")[0]
        where = frame[frame.find("(") + 1 : frame.rfind(":")].rsplit("/", 2)[-2:] if "(" in frame else [""]
        key = f"{name} [{'/'.join(where)}]"
        if key not in seen:
            seen.add(key)
            counts[key] += int(n)
print(f"samples={total}")
for key, n in counts.most_common(int(sys.argv[2]) if len(sys.argv) > 2 else 30):
    print(f"{100 * n / total:5.1f}%  {key}")
