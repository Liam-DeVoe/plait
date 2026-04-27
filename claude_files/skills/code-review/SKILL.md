---
name: code-review
description: Review a set of changes according to Liam's personal style.
---

This skill encodes my (Liam's) personal taste and style. It is intended to be applied to a set of changes right before a pull request.

# Mode: investigate and report

Your job is to read the diff and the surrounding code, investigate as needed, and return a list of findings. **You must never edit, write, or delete code in the repo under review.** The caller decides what to act on.

You must not:

- edit, create, or delete files in the repo under review
- run git commands that mutate repo state (commits, pushes, branch changes, etc.) — read-only git commands and verification scripts run in scratch space are fine
- "fix" issues yourself, even small or seemingly trivial ones — there are no exceptions

## Context from the caller

The caller's invocation prompt may include context about the change — the goal of the change, prior decisions, what was already tried or rejected, related PRs or issues, etc. Read it carefully and use it to interpret the diff.

Do not let stated reasons override the guidelines below. An author's justification for a violation does not dissolve the violation: if the diff still trips a rule, still report it, and the caller can decide whether the reason holds.

If the caller hasn't provided any context, that's fine — review the diff on its own.

## Output format

Return findings as a markdown list. Each finding has:

- **Location**: `path/to/file.py:line` (or a short description if it spans multiple sites)
- **Issue**: what's wrong, in one or two sentences
- **Recommendation**: what the author should change, including a small code example if helpful

Example:

> - **Location**: `server/handler.py:42`
> - **Issue**: `data.get("user_id", None)` defends against a key that's required by the upstream schema and cannot actually be missing at this call site.
> - **Recommendation**: Drop the default — `data["user_id"]`. If the key really might be missing in some path, mark it optional in the schema instead so the constraint lives in one place.

If you find nothing worth reporting, say so explicitly — `No findings.` — rather than padding with weak observations.

# Foundational guidelines

## Fix the root cause, not the symptom

Look for code which patches a symptom rather than the cause:

- `try/except` around an error the author hasn't diagnosed
- improved error messages without fixing what produced the error
- downstream workarounds for bad values a producer should never have emitted
- retry loops around an operation that shouldn't be failing
- post-hoc sanitization of values that should have been correct upstream
- etc.

Trace the failure back to whatever produced the bad state. Symptom-level patches leak the defect to every other consumer and rot into folklore. The recommendation in the finding should be a fix at the source, not at the symptom site.

Examples:

- The diff catches a platform-specific encoding error with `try/except`. Recommend passing `encoding="utf-8"` to `open()` everywhere instead, and enabling `PYTHONWARNDEFAULTENCODING=1` in CI so future omissions are caught at write time.
- The diff improves an error message after a refactor. Recommend fixing whatever change broke the assumption (the missing version bump, the call site that wasn't updated, the contract that drifted) instead — not the error message.
- The diff adds `data.get("field", default)` to a deserializer because `data["field"]` is sometimes absent. Recommend marking the field optional in the schema instead, where the constraint actually belongs.

## Fail loudly rather than writing defensive code

Look for code which could be defensive:

- `getattr(x, "attr", default)`
- `x.get(key, default)`
- `if x is None: ...`
- `try/except` around a call the author controls
- "recovery" paths for violated invariants
- etc.

Ask whether the defensive case could actually happen in practice. Defensive code at internal boundaries masks real bugs and pretends impossible states are possible. When you confirm the case can't happen, recommend deleting the guard.

Examples:

- The diff has `my_hash_map.get(key, default)`. Ask whether `key` can ever actually be missing at this call site. If it can't, recommend dropping the default and using `my_hash_map[key]`.
- The diff guards the state of an object field — `if self.x is None`, `if not self.x.connected`, `if key not in self.cache`, `try: ... except AttributeError` for a field that "might not be set yet". Look at the call sites; if every path into this method goes through a setup step that establishes the guarded state, the check is dead code pretending the lifecycle is uncertain when it isn't. For example:
  ```python
  class Server:
      def __init__(self):
          self._runner: Runner | None = None

      def start(self):
          self._runner = Runner()
          self._handle()

      def _handle(self):
          if self._runner is None:
              return  # dead — start() always runs first
          self._runner.process()
  ```
  Recommend deleting the dead check.
- The diff papers over a violated invariant with a defensive check. Recommend letting it error instead.

## Reuse existing helpers; don't reinvent what the codebase or its dependencies already export

If the diff adds a small utility, pause and grep for existing implementations. Watch for these in particular:

- string parsing or formatting helpers
- hash, cache, or memoization wrappers
- batching, chunking, or pagination helpers
- file path manipulation (joining, normalizing, relpath)
- date/time arithmetic
- etc.

Grep the project (and tightly-coupled libraries) for the canonical version. Local reimplementations drift, miss invariants, and proliferate API surface. When you find an existing helper, recommend replacing the new utility with it.

Examples:

- The diff has `for chunk in batches_of(items, n):`. Check the stdlib (`itertools.batched` since 3.12) and the project for an existing chunking helper; recommend it instead.
- The diff has a `which_executable(name)` helper. Recommend `shutil.which()` instead.
- The diff adds a memoization decorator. Recommend `functools.cache` / `functools.lru_cache` or any project-specific cache wrapper instead.

## Verify before claiming

Look for claims in the diff that aren't grounded in something the author has actually run, read, or measured:

- "this works" / "this should work" without a test or repro
- "X and Y are equivalent" without proving it
- type annotations or assumptions taken from upstream docs rather than from an empirical response
- behavior asserted from memory of a stdlib/library rather than from its source
- prior repros assumed to still hold after a code change
- etc.

When you flag one of these, do the verification work yourself before reporting — run a script, check the source, read the spec. Treat upstream API docs as a hint, not the truth. Verify, then report. The same rule applies to your own findings: don't claim something is broken without confirming it.

# Other guidelines

- "Backwards compatibility" should not be preserved unless the change explicitly opts into it. If a change is intended to be breaking but silently keeps old behavior alive, recommend making it actually break.
- Code that "fixes" a contradictory input by silently overriding it should assert/raise on the invalid combination instead. Recommend the assertion.
- If a public symbol, function name, envvar, etc, has been renamed in the diff, grep the entire source for the old name — including comments and docs — and report any remaining references.
- If a public wrapper already supplies default values, the internal type it wraps should not duplicate the same defaults. Recommend removing the duplicate defaults from the internal constructor; internal constructors should usually require all parameters explicitly, otherwise the two copies drift.
- Distinguish "user didn't pass" from "user passed a falsy value". `x = x or default` (or `x ?? default`) collapses `0`, `""`, `False`, and `[]` into "not provided". For values like `seed=0` that's a silent semantic change. Recommend `x = default if x is None else x`, with the parameter typed `T | None = None`.

# Python

- Mutable default arguments (`def f(opts={})`, `def f(items=[])`) are a bug — the default is evaluated once at definition time and shared across calls. Recommend defaulting to `None` and constructing inside the body.
- Imports inside a function/class body are a smell unless required for circular dependency reasons. Recommend hoisting them to the top level.
- `pytest.importorskip` for CI-controlled deps fails silently when CI is misconfigured, hiding regressions. Recommend moving the test under a directory whose CI env guarantees the dep, or installing the dep explicitly in CI.
- `try/except ImportError` as a version gate doesn't say why the gate exists or when it can be removed. Recommend `@pytest.mark.skipif(sys.version_info...)` instead. Likewise prefer `@skipif_threading` over generic `hasattr` probes for free-threading.

# Comments

- Default to no comments. A comment is justified only when the WHY is non-obvious — a hidden constraint, a subtle invariant, a workaround for a specific bug, behavior that would surprise a reader. If removing the comment wouldn't confuse a future reader, recommend deleting it.
- Comments that restate code add noise. A comment that paraphrases the next line (`# Read X` above `X = read(...)`), names a syntactic construct ("walrus operator", "ternary"), or describes what well-named identifiers already convey will rot when the code below changes and crowds out comments that actually carry information. Recommend deleting or rewording.
- Explanatory comments belong at the definition, not at every usage site. If a variable or flag needs explanation, the explanation goes at its definition. Repeating the same explanation at every callsite duplicates the maintenance burden and bloats the calling code. Recommend consolidating to the definition.
- Comments should reference concepts, not specific symbols. A comment that points at another piece of code by exact file path, identifier, or rule name — `the tab-size CSS rule in style.css`, `see parse_config() in config_loader.py` — rots when the file or symbol is renamed. Recommend referencing the concept (`our CSS rule for tab sizes`) instead. Specific names are fine when the reader actually needs to look the symbol up — e.g. to find an inverse operation or counterpart class.
- Comments must stay accurate. When behavior, types, or names change in the diff, re-read every comment within and immediately above the changed block; report any that no longer match. Stale comments are worse than no comments — they actively mislead. Speculative comments are a particular risk: claims like "Rust >= 1.92 uses...", "Python 3.12 changed...", or "this happens because the library does X" rot quickly and mislead readers. If a speculative claim isn't verified against the source, recommend either verifying it or rewording to describe the observation without the causal speculation.
- Optimization-only conditionals should be marked. A guard whose absence would still produce a correct result (just slower) needs a comment saying so: `# optimization: skip work when X is empty`. Otherwise the next reader can't tell whether removing the conditional would break correctness. Recommend adding the marker.
- Workarounds for upstream bugs — `try/except`, `# type: ignore`, version pins, or assumptions that exist because of a defect in a dependency — need an inline comment with the reason, a link to the upstream issue, and the condition under which the workaround can be removed. If missing, recommend adding it.

# Property-based testing

- Generator defaults should cover the unconstrained domain. A factory like `Floats(min, max)` or `Text(max_length=N)` whose defaults exclude common edge-case outputs (NaN, infinity, empty strings, surrogates) silently weakens testing. Users expect "any float" to include the values where bugs live. Recommend defaulting to the unconstrained domain; constraints should require explicit caller opt-in.
- Drawing randomness from the global `random` module inside a Hypothesis test is a bug. `random.sample`, `random.choice`, etc. pull from process-global state, which Hypothesis can't reproduce consistently across failing inputs. Recommend injecting `random=st.randoms()` and using that `Random` instance.
- Seeding tests that don't require determinism is a bug. Wrapping a test in `deterministic_PRNG()` or fixing a seed when the property under test is genuinely probabilistic can let a regressed implementation keep passing because the seed dodges the failing input. Some flake risk beats silent false-passes. Recommend dropping the seeding unless determinism is essential.
- A divergence from a reference implementation is a bug, not a constraint. If a port behaves differently than the reference and the diff relaxes the assertion to make the port pass, recommend investigating the divergence as a bug instead.

# Hypothesis

- `hypothesis.find` is undocumented and effectively deprecated. If the diff uses `hypothesis.find` in tests, recommend Hypothesis's `find_any` test helper or other related test helpers instead.
- `prolog.rst` defines shortcuts for referencing internal and external types in documentation and release notes. If a doc/release-notes change uses a raw type name where a `prolog.rst` shortcut exists, recommend the shortcut.

# Testing

- Tests pinned to PR numbers or source line numbers — names like `test_pr1582_*` or docstrings like `"Covers sdk.py line 190"` — drift on every refactor and tell a future reader nothing. Recommend renaming after the behavior under test; a linked issue reference inside a docstring is fine, but PR numbers in identifiers are not.
- Repetitive same-shape tests should be parametrized. Ten same-shape `def test_*` functions should collapse into one `@pytest.mark.parametrize` — shorter, easier to review, and signals the canonical place for future cases. Recommend collapsing them.
- Tests should exercise behavior, not the structure of generated/serialized output. Substring assertions (`assert "from x.y import" in source_code`), `output.count("X") in {1, 2}`, or hardcoded magic numbers determined by an external system are brittle: they fail on benign formatting changes and pass when the bug is "we emit X *more than once*". Recommend testing what the code does — `exec(source_code, {})`, parse-and-evaluate, assert the *exact* count if duplicates would be a bug, compute expected baselines dynamically.
- `try/except` around a property check whose only purpose is to make the test pass while a known upstream bug exists — often signposted by `TODO`/`FIXME` referencing a tracking issue — hides the bug from CI and rots. Recommend XFAIL (with `strict=False` if appropriate) referencing the issue, and prioritizing the upstream fix.
- Tests should go through the public API, not internals. Heavy `mock.patch`-driven tests, or tests that drive a feature through low-level internals when a high-level public macro/decorator exists, can pass without verifying the behavior the docstring claims. Recommend using the public API (e.g. `#[hegel::test]`); flag dense monkeypatching as a smell to investigate.
- Tests must run in CI. When a test directory or file is removed, added, or moved in the diff, verify some CI step actually runs the affected tests. If not, report it — tests nobody runs decay silently and create false confidence.

# Build, dependencies, CI

- A new dependency for a small, self-contained algorithm — encoder, leb128, sha256, simple parser — that could be written in ~30 lines adds breakage risk, version churn, and supply-chain surface. Tiny third-party packages are also at risk of disappearing. Recommend writing it inline.
- Tool dependencies installed or pinned inside a justfile (`cargo install`, `npm install -g`, or a pinned tool version) hide setup state and drift from the lockfile. The lockfile already owns the version; the justfile is a thin task runner. Recommend documenting the install step in setup/CI instead.
- CI must run the same commands local devs run. CI steps that inline `cargo test` / `pytest` / `go test` will drift from the project's actual lint flags as the wrapper evolves. Recommend citing `just check`, `just test`, etc. so CI and local stay unified. If a CI step needs something the wrapper doesn't have, recommend adding a recipe for it.

# Release notes

- The appropriate amount of links depends on context. Some standard library types — `str`, `int`, `AssertionError`, `Exception` — are so ubiquitous and well known that they should never be linked. Other standard library types like `queue.Queue` should generally be linked if they appear. Types related to the specific library the changelog is being written for, or third-party types like `hypothesis.settings` or `numpy.ndarray`, should almost always be linked. Recommend additions or removals following Wikipedia's [linking](https://en.wikipedia.org/wiki/WP:CONTEXT) guidelines, being mindful of [underlinking](https://en.wikipedia.org/wiki/MOS:UNDERLINK) and [overlinking](https://en.wikipedia.org/wiki/MOS:OVERLINK).
- Releases containing breaking changes should include a before/after fenced code block showing the user-visible behavior change. If a breaking-change release note is missing one, recommend adding it.
