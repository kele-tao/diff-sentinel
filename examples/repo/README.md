# demo-service

A deliberately tiny service used by the diff-sentinel examples. It is the "before"
state: `examples/demo.py` copies this tree into a scratch git repository, commits it,
applies `examples/repo.patch` as a second commit, and then reviews the difference.
