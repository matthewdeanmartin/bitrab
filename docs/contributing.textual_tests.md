# Testing Textual apps: a contributor’s guide

Textual has first-class testing support built around running an app in a test harness and driving it with a `Pilot`. The core entry point is `App.run_test()`, which is an **async** context manager intended for tests; it runs the app headlessly by default and lets you control the app with a `Pilot` object. Textual’s own testing guide centers on simulating user input with methods like `Pilot.press()` and `Pilot.click()`, then asserting on app state. ([Textual Documentation][1])

## Recommended stack

For most projects, the sensible default stack is:

* **pytest** as the test runner
* **Textual’s built-in test harness** via `app.run_test()`
* **pytest-textual-snapshot** for visual regression tests
* **pytest’s `monkeypatch` fixture** for targeted patching of environment, globals, and small seams
* **`unittest.mock`** when you need richer mocks, call assertions, or spec-constrained doubles ([Textual Documentation][2])

That combination matches Textual’s official guidance: interactive behavior tests with `run_test()`/`Pilot`, and snapshot testing with the official `pytest-textual-snapshot` plugin, which captures SVG screenshots and compares them across runs to catch visual regressions. ([Textual Documentation][2])

## What a basic Textual unit test looks like

The basic pattern is:

1. Construct the app.
2. Enter `async with app.run_test() as pilot:`
3. Simulate keys or clicks.
4. Assert on app state, widget state, or rendered behavior. ([Textual Documentation][2])

Example skeleton:

```python
import pytest

from myapp.app import MyApp


@pytest.mark.asyncio
async def test_submit_button_enables_after_typing() -> None:
    app = MyApp()

    async with app.run_test() as pilot:
        await pilot.press("h", "e", "l", "l", "o")
        submit = app.query_one("#submit")
        assert not submit.disabled
```

Why async? Because `run_test()` itself is async and Textual’s testing guide explicitly notes that tests using it must run in a coroutine. ([Textual Documentation][2])

## The most useful Textual testing APIs

### `run_test()`

`run_test()` is the standard harness for testing apps. It runs headless by default, allows a fixed terminal size, and can optionally enable tooltips and notifications during tests. It also accepts a `message_hook`, which is a callback invoked whenever a message arrives at a message pump in the app. That hook is extremely useful for deep debugging of event/message flow. ([Textual Documentation][1])

### `Pilot.press()`

Use this to simulate key presses. Textual supports passing multiple key values so you can model typing sequences, not just one keystroke at a time. ([Textual Documentation][2])

### `Pilot.click()`

Use this to simulate mouse clicks against a widget selected by CSS selector. One important trap: if another widget is visually on top, the click may land on that topmost widget instead. That is intentional and mirrors real user behavior. ([Textual Documentation][2])

## When to write “unit” tests vs snapshot tests

Use ordinary `run_test()`-style tests when you care about:

* keybindings
* button behavior
* widget state
* actions and commands
* validation
* messages/events
* focus changes
* app logic ([Textual Documentation][2])

Use **snapshot tests** when you care about:

* layout regressions
* styling regressions
* visual state changes
* subtle rendering changes that are hard to express as assertions ([Textual Documentation][2])

Snapshot testing in Textual is based on SVG screenshots. The official plugin is `pytest-textual-snapshot`, and Textual notes that it uses this approach internally for builtin widgets as part of release validation. ([Textual Documentation][2])

## Suggested testing strategy for contributors

A practical pyramid for Textual projects:

### 1. Fast interaction tests

These should be the bulk of the suite. Drive the app with `Pilot`, assert on widget state, message effects, and app properties. They are usually less brittle than visual snapshots. This is the core testing style shown in Textual’s guide. ([Textual Documentation][2])

### 2. Snapshot tests for important screens

Add snapshots for the main screens, custom widgets, and historically fragile layouts. Since the plugin stores screenshots and compares them later, it is good at catching visual drift. ([Textual Documentation][2])

### 3. A few concurrency/worker tests

If the app uses background work, test the user-visible effect of worker completion, cancellation, and error states. Textual’s worker guide exists because concurrent work is a common and tricky part of real apps. ([Textual Documentation][3])

## Is mocking a good idea?

Usually: **some mocking is good; lots of mocking is bad**.

Textual apps are UI-driven and event-driven. If you mock too much of Textual itself, you can easily end up testing your own expectations rather than the real app behavior. The official Textual approach is already a kind of high-fidelity harness: run the real app, send real inputs, assert on real state. ([Textual Documentation][2])

### Good uses of mocking / patching

Use mocking or patching for things outside the UI framework:

* HTTP calls
* filesystem access that you do not want to hit for real
* environment variables
* clocks / timestamps
* subprocesses
* expensive backends or services
* feature flags and configuration seams ([pytest][4])

### Bad uses of mocking

Be cautious about mocking:

* Textual internals
* widget methods just to “prove” they were called
* event/message plumbing that can be observed through state
* rendering behavior that should really be covered by snapshots or real app assertions

### Preferred patching tools

For simple cases, `pytest`’s `monkeypatch` is excellent because it safely restores changes after the test and directly supports attrs, dicts, env vars, `sys.path`, and cwd changes. ([pytest][4])

For richer mocks, `unittest.mock` is still useful. If you use it, prefer `spec` or `spec_set` so your mocks fail when your assumptions drift from the real object API. ([Python documentation][5])

## Strong recommendation: test user-visible outcomes

For a Textual contributor unfamiliar with the framework, the safest mental model is:

> Prefer asserting on **state and behavior the user would care about**, not on implementation trivia.

Examples:

Good:

```python
assert app.query_one("#save").disabled is False
assert app.query_one("#status").renderable.plain == "Saved"
```

Weaker:

```python
mock_save.assert_called_once()
```

The second can still be appropriate, but only when the outward result is hard to observe directly.

## Common traps

### 1. Forgetting tests must be async

If you use `run_test()`, your test must run as a coroutine. This is one of the easiest mistakes for new contributors. ([Textual Documentation][2])

### 2. Clicking hidden or covered widgets

`Pilot.click()` follows visible screen behavior. If something overlays the target, your click may hit the overlay instead of the intended widget. That is realistic, but it surprises people. ([Textual Documentation][2])

### 3. Not controlling terminal size

`run_test()` accepts a `size` argument. Layout-sensitive tests can become flaky if you implicitly depend on terminal geometry and do not fix it in the harness. ([Textual Documentation][1])

Example:

```python
async with app.run_test(size=(100, 30)) as pilot:
    ...
```

### 4. Treating snapshot tests like ordinary assertions

Snapshot tests are powerful, but they are not always the best first tool. They can fail on legitimate UI changes and require review. Use them where visuals matter, not for every single behavior. The plugin is specifically about SVG screenshot comparison, so it is best for visual regressions. ([Textual Documentation][2])

### 5. Ignoring concurrency

Textual widgets run in an async environment, and workers exist because apps often need background tasks such as network or subprocess work. Tests that touch those areas need to be written around eventual UI outcomes, not purely synchronous assumptions. ([Textual Documentation][6])

### 6. Over-mocking async/concurrent code

If background work is central to the feature, replacing too much with mocks can hide timing, sequencing, or message-flow bugs. Patch the external dependency, but let the Textual app and worker machinery run for real where practical. That advice follows from Textual’s own emphasis on real async UI behavior and worker-driven concurrency. ([Textual Documentation][3])

## Useful patterns

### Pattern: test through selectors

Use stable widget IDs or classes and query them directly from the app:

```python
name_input = app.query_one("#name")
save_button = app.query_one("#save")
```

This keeps tests readable and avoids depending on fragile tree positions.

### Pattern: fix the screen size

For layouts or anything responsive:

```python
async with app.run_test(size=(120, 40)) as pilot:
    ...
```

That uses a documented feature of `run_test()` and removes a whole class of flaky failures. ([Textual Documentation][1])

### Pattern: use `message_hook` when debugging hard failures

`run_test()` has a `message_hook` callback that receives every message. That can help contributors understand why a handler did not fire or why a state change never happened. ([Textual Documentation][1])

Example:

```python
messages: list[str] = []

def hook(message) -> None:
    messages.append(type(message).__name__)

async with app.run_test(message_hook=hook) as pilot:
    await pilot.press("enter")

assert "ButtonPressed" in messages
```

### Pattern: patch boundaries, not core UI

Good:

```python
def test_loads_data(monkeypatch):
    monkeypatch.setenv("API_URL", "http://test")
```

Also good:

```python
from unittest.mock import Mock

client = Mock(spec_set=["fetch_items"])
client.fetch_items.return_value = ["a", "b"]
```

That uses patching where pytest and Python docs say it shines: globals, env, and dependency seams. ([pytest][4])

## Libraries to use

### Definitely use

* `pytest`
* `textual`
* `pytest-textual-snapshot` for visual regression tests
* `unittest.mock` from the stdlib
* pytest’s built-in `monkeypatch` fixture ([Textual Documentation][2])

### Often useful

* `pytest-xdist` if you want to parallelize pytest runs; the snapshot plugin’s README notes it can be used in parallel test runs. ([GitHub][7])

## A practical house style for Textual tests

For a contributor guide, I would recommend these rules:

1. **Prefer pytest over unittest style.**
2. **Use `run_test()` for nearly all interaction tests.**
3. **Assert on widget/app state after real inputs.**
4. **Use snapshot tests for visuals, not everything.**
5. **Patch only external seams.**
6. **Prefer `monkeypatch` for simple test-local patching.**
7. **Use `Mock(spec=...)` or `spec_set=...` when you do mock.**
8. **Fix terminal size in layout-sensitive tests.**
9. **Give important widgets stable IDs for querying in tests.**
10. **Use `message_hook` when event/message flow gets confusing.** ([Textual Documentation][1])

## Example contributor boilerplate

```python
import pytest

from myapp.app import MyApp


@pytest.mark.asyncio
async def test_typing_enables_submit() -> None:
    app = MyApp()

    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.press("h", "e", "l", "l", "o")
        assert app.query_one("#submit").disabled is False


@pytest.mark.asyncio
async def test_clicking_save_updates_status(monkeypatch) -> None:
    app = MyApp()

    monkeypatch.setenv("MYAPP_OFFLINE", "1")

    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.click("#save")
        assert app.query_one("#status").renderable.plain == "Saved"
```

This style stays close to Textual’s official testing model and keeps the tests understandable for contributors who are new to the framework. ([Textual Documentation][2])

## Bottom line

For Textual, the best default is **real app + real simulated input + ordinary assertions**, with **snapshot tests** for visuals and **light mocking** only at boundaries like HTTP, subprocesses, filesystem, config, and environment. That gets you close to how the app actually behaves while still keeping tests fast and maintainable. ([Textual Documentation][2])

I can also turn this into a polished Markdown contributor doc with sections like “Do/Don’t,” “Test recipes,” and a starter `tests/` layout.

[1]: https://textual.textualize.io/api/app/ "textual.app - Textual"
[2]: https://textual.textualize.io/guide/testing/ "Testing - Textual"
[3]: https://textual.textualize.io/guide/workers/ "Workers - Textual"
[4]: https://docs.pytest.org/en/stable/how-to/monkeypatch.html "How to monkeypatch/mock modules and environments - pytest documentation"
[5]: https://docs.python.org/3/library/unittest.mock.html "unittest.mock — mock object library — Python 3.14.3 documentation"
[6]: https://textual.textualize.io/?utm_source=chatgpt.com "Textual"
[7]: https://github.com/Textualize/pytest-textual-snapshot?utm_source=chatgpt.com "pytest-textual-snapshot"
