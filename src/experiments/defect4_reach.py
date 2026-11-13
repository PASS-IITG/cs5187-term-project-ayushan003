"""Can any generated case reach defect 4? Prints a JSON summary.

Defect 4 needs a pad length L with L <= 10**9 characters (so the first patch's
character check passes) and a result of more than 10**9 bytes (so the byte
limit is exceeded). With a fill of at most 4 bytes per character that means
250,000,000 <= L <= 1,000,000,000. This script counts the cases, over seeds
0-19999 in each mode, that contain any integer literal in that range.

    python -m experiments.defect4_reach 2>/dev/null > experiments/defect4-reach.json
"""

from __future__ import annotations

import json
import random
import re

from graft.campaign import Campaign

LO, HI = 250_000_000, 1_000_000_000
INTEGER = re.compile(r"(?<![\w.])-?\d+(?![\w.])")


def main() -> None:
    """Scan every mode over the ablation's seed range and print the counts."""
    modes = {}
    for mode in ("variants", "fuzzer", "both"):
        campaign = Campaign("turso", mode=mode, engines={})
        cases = in_range = 0
        for seed in range(20000):
            case = campaign.next_case(random.Random(seed))
            if case is None:
                continue
            setup, query = case[0], case[1]
            cases += 1
            text = "\n".join(list(setup) + [query])
            if any(LO <= abs(int(v)) <= HI for v in INTEGER.findall(text)):
                in_range += 1
        modes[mode] = {"cases": cases, "cases_with_integer_in_range": in_range}
    print(json.dumps({"range": [LO, HI], "seeds": "0-19999", "modes": modes}, indent=1))


if __name__ == "__main__":
    main()
