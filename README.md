# E2E Flow Test Agent

An AI-assisted end-to-end testing agent. It scans a web application, asks Gemini to write Selenium + Pytest tests for user flows, runs them, and **self-heals** broken selectors by re-scanning the live page. A Streamlit dashboard shows the generated code, the healing diffs and a PASS/FAIL report.

Built for the Incture Hackathon (Bhubaneswar). The demo target is the public [SauceDemo](https://www.saucedemo.com) shop.

## What it does

```
scan pages  ->  Gemini writes a test per flow  ->  run with Pytest
                                                        |
                                       fails? -> re-scan live page -> repair test
                                                 (up to 2 attempts)   -> re-run
                                                        |
                                                  PASS/FAIL report
```

1. **Scan.** Opens the login, products and cart pages in headless Chrome (logging in before the protected pages) and records each visible element's `id`, `name`, `type`, `placeholder`, `data-test` and text.
2. **Generate.** Sends that metadata and a plain-English flow goal to Gemini, which returns one Pytest file using Selenium 4 and `WebDriverWait`. If Gemini is unavailable, the agent switches to built-in template tests so the run still completes.
3. **Run.** Each generated test runs as a separate `pytest` subprocess with assertions on the result.
4. **Self-heal.** On failure the agent re-scans the live site and repairs the test, up to 2 times. It first asks Gemini to repair it. If Gemini is down, a rule-based healer uses `difflib` to swap any selector that no longer exists for the closest real `id`, `name` or `data-test` value (similarity cutoff 0.8).
5. **Report.** Writes `report.txt` with the result, who wrote each test, how many heals were used and the total time.

### Flows tested

| Flow | Goal |
|---|---|
| `login` | Log in with valid credentials and verify the products page is shown |
| `add_to_cart` | Log in, add the first product, open the cart and verify that product is listed |

## Quick start

**Requirements:** Python 3.10+, Google Chrome, and a [Gemini API key](https://aistudio.google.com). Selenium 4 downloads the matching ChromeDriver automatically.

```bash
git clone https://github.com/suraj-6/qualitytesttingproject.git
cd qualitytesttingproject
python -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env              # Windows: copy .env.example .env
# edit .env and add your GEMINI_API_KEY
```

### Run from the command line

```bash
python e2e_agent.py                    # generate, run and report
python e2e_agent.py --break            # also simulate a UI change to demo self-healing
python e2e_agent.py --offline --break  # no Gemini: templates + rule-based healer only
```

Set `HEADLESS=0` to watch the browser (`export HEADLESS=0` on Mac/Linux, `set HEADLESS=0` on Windows).

### Run the dashboard

```bash
streamlit run app.py
```

The dashboard shows the discovered page flow, each flow's generated code, a diff of what self-healing changed, and a summary of flows passed, heals used and time taken. You can switch Gemini off and toggle the simulated UI change from the sidebar.

## Example output

From a run with a simulated UI change (`--break`):

```
E2E FLOW TEST AGENT - REPORT
Flow map: /  ->  /inventory.html  ->  /cart.html

login          PASS   written by: AI (Gemini)  | self-heals: 1 (by rule-based (selector matching))
add_to_cart    PASS   written by: built-in template  | self-heals: 0 (by -)

Time taken: 210 seconds
```

In this run the login test was written by Gemini and broke when its login-button ID was renamed. The rule-based healer found the closest real ID and the test passed again. The `add_to_cart` test came from the built-in template because Gemini was unavailable at that point.

## Configuration

Set these in `.env`:

| Variable | Required | Purpose |
|---|---|---|
| `GEMINI_API_KEY` | Yes | Gemini API key |
| `GEMINI_MODEL` | No | Preferred model. If it is busy or unavailable, the agent retries and falls back to `gemini-2.5-flash`, then `gemini-2.5-flash-lite` |
| `HEADLESS` | No | `1` (default) runs Chrome headless, `0` shows the window |

Never commit `.env`. It is listed in `.gitignore`.

## Companion tool: AI unit-test generator

`qa_gen_agent.py` applies the same generate, run and repair loop to unit tests:

```bash
python qa_gen_agent.py your_module.py
```

It reads the module, has Gemini write Pytest tests (normal, edge and error cases), runs them with `pytest-cov` coverage, and sends any failures back to Gemini to fix, up to 3 attempts. It then prints the result, number of attempts and time taken, and flags tests that still fail for human review.

## Project structure

```
e2e_agent.py         # scan, generate, run, self-heal, report (CLI)
app.py               # Streamlit dashboard
qa_gen_agent.py      # unit-test generator with coverage feedback loop
generated_tests/     # tests written by the agent (examples from a run)
requirements.txt
.env.example         # template for your API key
```

## Limitations

This is a hackathon prototype, so know what it does not do yet:

- **Small scope.** Two flows and three fixed pages on one demo site. It scans a hardcoded page list and does not follow links.
- **Simulated UI change.** `--break` renames an element ID inside the test file. The web page itself is not changed.
- **Healing can hide real bugs.** Any failing test triggers a repair attempt. The agent does not yet separate a changed locator from a genuine application defect, so a failed assertion could be "healed" instead of reported.
- **No metrics yet.** There are no measured numbers for first-run pass rate or heal success across many runs.

## Roadmap

- Classify failures: heal only locator errors (`NoSuchElement`, timeouts) and report assertion failures as defects.
- Add negative flows (locked-out user, wrong password) and LLM-generated test data.
- Check generated code before running it (it parses and contains an assert).
- Measure first-run pass rate and heal success over repeated runs.
- Add a Playwright output option and a GitHub Actions workflow.

## Tech stack

Python, Selenium 4, Pytest, pytest-cov, Google Gemini API (`google-genai`), Streamlit, `difflib`.
