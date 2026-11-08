---
title: Related tools
description: Choose pytest-quint-connect, pytest-selective, CI-esta, dry-lint, Due or pytest-obligation for the problem you need to solve.
---

# Choose the tool for your problem

| Your problem | Tool | What you gain |
| --- | --- | --- |
| Pull requests re-implement functions the codebase already has | [dry-lint](https://dry-lint.readthedocs.io/en/latest/) | Judged duplicate findings on each pull request and a ratcheted backlog |
| Every edit reruns unrelated Python tests | [pytest-selective](https://pytest-selective.readthedocs.io/en/latest/) | Explained affected-test selection or complete-lane routing, with conservative fallbacks |
| CI is slow and you need to know why | [CI-esta](https://ci-esta.readthedocs.io/en/latest/) | Job and step reports, dependency critical paths and an honest required gate |
| Background work can be lost or repeated after a crash | [Due](https://due.readthedocs.io/en/latest/) | Durable Django work tables, recovery, and explicit completion evidence |
| Passing tests miss failures during retries or recovery | [pytest-obligation](https://pytest-obligation.readthedocs.io/en/latest/) | Executable crash histories, fault scenarios, and checks for declared guarantees |
| Your Python code should match a formal model | [pytest-quint-connect](https://pytest-quint-connect.readthedocs.io/en/latest/) | Quint-generated histories replayed against your implementation under pytest |

pytest-quint-connect's own CI uses pytest-selective to pick the work a change
can affect, CI-esta for its required gate and run reports, and dry-lint to keep
duplicated functions out of its pull requests ([how CI runs here](../docs/ci-toolkit.md)).
Its first production contract checks pytest-obligation's report merge.
