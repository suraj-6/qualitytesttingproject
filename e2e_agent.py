"""E2E Flow Test Agent prototype (Use Case 10).

Run:            python e2e_agent.py
Healing demo:   python e2e_agent.py --break
No AI needed:   python e2e_agent.py --offline --break
Show browser:   set HEADLESS=0 (Windows)  /  export HEADLESS=0 (Mac/Linux)

Steps: crawl site -> AI writes Selenium tests per flow -> run them
       -> if a test fails, re-crawl and ask AI to repair it -> report
"""
import difflib
import json
import os
import pathlib
import re
import subprocess
import sys
import time

from google import genai
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By


def load_env_file():
    env_path = pathlib.Path(__file__).with_name(".env")
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))

    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("Missing GEMINI_API_KEY. Add it to the environment or the project .env file.")
    return api_key


load_env_file()
MODEL = os.getenv("GEMINI_MODEL", "gemini-3.8-flash")
BASE = "https://www.saucedemo.com"
USER, PWD = "standard_user", "secret_sauce"
PAGES = ["/", "/inventory.html", "/cart.html"]  # page 2 and 3 need login
FLOWS = [
    {"name": "login",
     "goal": "Log in with valid credentials and verify the products page is shown"},
    {"name": "add_to_cart",
     "goal": "Log in, add the first product to the cart, open the cart and verify that product is listed"},
]
OUT = pathlib.Path("generated_tests")
OUT.mkdir(exist_ok=True)
client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))


# ---------- browser helpers ----------
def new_driver():
    opts = Options()
    if os.getenv("HEADLESS", "1") == "1":
        opts.add_argument("--headless=new")
    return webdriver.Chrome(options=opts)


def login(driver):
    driver.get(BASE + "/")
    driver.find_element(By.CSS_SELECTOR, "input[type=text]").send_keys(USER)
    driver.find_element(By.CSS_SELECTOR, "input[type=password]").send_keys(PWD)
    driver.find_element(By.CSS_SELECTOR, "input[type=submit], button[type=submit]").click()
    time.sleep(1)


def describe(driver):
    """List the interesting elements on the current page."""
    items = []
    for el in driver.find_elements(By.CSS_SELECTOR, "input, button, a, select, [data-test]"):
        if not el.is_displayed():
            continue
        info = {k: el.get_attribute(k) for k in ("id", "name", "type", "placeholder", "data-test")}
        info = {k: v for k, v in info.items() if v}
        info["tag"] = el.tag_name
        info["text"] = (el.text or "")[:40]
        items.append(info)
    return items


def crawl_site():
    driver = new_driver()
    site = {}
    try:
        for i, path in enumerate(PAGES):
            driver.get(BASE + path)
            if i == 1:  # need to be logged in from here on
                login(driver)
                driver.get(BASE + path)
            site[path] = describe(driver)
    finally:
        driver.quit()
    return site


# ---------- AI helpers ----------
# If the main model is busy (503/429), wait, retry and switch to these backups.
FALLBACK_MODELS = ["gemini-2.5-flash", "gemini-2.5-flash-lite"]
BUSY_SIGNS = ("503", "429", "500", "UNAVAILABLE", "RESOURCE_EXHAUSTED", "NOT_FOUND", "404")


def generate_with_retry(prompt, tries=4):
    models = [MODEL] + [m for m in FALLBACK_MODELS if m != MODEL]
    for attempt in range(tries):
        model = models[attempt % len(models)]
        try:
            return client.models.generate_content(model=model, contents=prompt).text
        except Exception as err:  # noqa: BLE001
            if attempt == tries - 1 or not any(s in str(err) for s in BUSY_SIGNS):
                raise
            wait = 5 * (attempt + 1)
            print(f"   Gemini busy ({model}); retrying in {wait}s (try {attempt + 2}/{tries})...")
            time.sleep(wait)


def ask_ai(prompt):
    text = generate_with_retry(prompt)
    match = re.search(r"```(?:python)?\n(.*?)```", text, re.S)
    return match.group(1) if match else text


RULES = (
    "Rules: Selenium 4 + pytest. Create a Chrome driver in a pytest fixture; add "
    "'--headless=new' only if os.getenv('HEADLESS','1')=='1'. Use WebDriverWait for every "
    "element. Use the ids / data-test values from the crawler data. One test function that "
    "ends with a clear assert. Return only one python code block."
)


def generate(flow, site):
    return ask_ai(
        f"Write ONE pytest test file for this flow on {BASE}.\n"
        f"Flow: {flow['goal']}\nCredentials: {USER} / {PWD}\n"
        f"Elements found by the crawler, per page: {json.dumps(site)}\n{RULES}"
    )


def heal(flow, code, error, site):
    return ask_ai(
        f"This Selenium test broke, probably because the web page changed.\n"
        f"Flow: {flow['goal']}\n\nTEST CODE:\n{code}\n\nERROR:\n{error}\n\n"
        f"FRESH elements from the live site, per page: {json.dumps(site)}\n"
        f"Fix the selectors/assertions. Return the full fixed file.\n{RULES}"
    )


# ---------- fallback when the AI is unavailable ----------
AI_DOWN = False  # becomes True after the AI fails once, so we do not keep waiting

TEMPLATE_HEAD = """import os
import pytest
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait


@pytest.fixture
def driver():
    opts = Options()
    if os.getenv("HEADLESS", "1") == "1":
        opts.add_argument("--headless=new")
    d = webdriver.Chrome(options=opts)
    yield d
    d.quit()


def do_login(driver, wait):
    driver.get("https://www.saucedemo.com/")
    wait.until(EC.presence_of_element_located((By.ID, "user-name"))).send_keys("standard_user")
    driver.find_element(By.ID, "password").send_keys("secret_sauce")
    wait.until(EC.element_to_be_clickable((By.ID, "login-button"))).click()
"""

TEMPLATES = {
    "login": TEMPLATE_HEAD + """

def test_login(driver):
    wait = WebDriverWait(driver, 10)
    do_login(driver, wait)
    wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, ".inventory_item")))
    assert "inventory.html" in driver.current_url
""",
    "add_to_cart": TEMPLATE_HEAD + """

def test_add_to_cart(driver):
    wait = WebDriverWait(driver, 10)
    do_login(driver, wait)
    first_name = wait.until(
        EC.presence_of_element_located((By.CSS_SELECTOR, ".inventory_item_name"))).text
    wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, "button.btn_inventory"))).click()
    driver.find_element(By.CSS_SELECTOR, ".shopping_cart_link").click()
    item = wait.until(EC.presence_of_element_located(
        (By.CSS_SELECTOR, ".cart_item .inventory_item_name")))
    assert item.text == first_name
""",
}


def rule_based_heal(code, site):
    """Self-healing without AI: find selectors in the test that no longer exist on the
    live page and swap them for the closest-matching real id / name / data-test value."""
    known = set()
    for els in site.values():
        for el in els:
            for key in ("id", "name", "data-test"):
                if el.get(key):
                    known.add(el[key])
    changes = []
    for lit in sorted(set(re.findall(r"""["']#?([A-Za-z0-9_-]{4,60})["']""", code))):
        if lit in known or lit in (USER, PWD):
            continue
        close = difflib.get_close_matches(lit, known, n=1, cutoff=0.8)
        if close:
            code = code.replace(lit, close[0])
            changes.append((lit, close[0]))
    return code, changes


def generate_or_template(flow, site, offline):
    """Try the AI first; if it is down, use the built-in template test."""
    global AI_DOWN
    if not offline and not AI_DOWN:
        try:
            return generate(flow, site), "AI (Gemini)"
        except Exception as err:  # noqa: BLE001
            AI_DOWN = True
            print(f"   AI unavailable ({str(err)[:60]}...). Switching to built-in templates.")
    if flow["name"] in TEMPLATES:
        return TEMPLATES[flow["name"]], "built-in template"
    raise RuntimeError(f"No AI and no template for flow '{flow['name']}'")


def heal_or_rules(flow, code, error, fresh, offline):
    """Try AI healing first; if it is down, heal with the rule-based healer."""
    global AI_DOWN
    if not offline and not AI_DOWN:
        try:
            return heal(flow, code, error, fresh), "AI (Gemini)"
        except Exception as err:  # noqa: BLE001
            AI_DOWN = True
            print(f"   AI unavailable ({str(err)[:60]}...). Using rule-based healer.")
    new_code, changes = rule_based_heal(code, fresh)
    for old, new in changes:
        print(f"   Healed selector: '{old}' -> '{new}'")
    return new_code, "rule-based (selector matching)"


# ---------- test running ----------
def run(test_path):
    p = subprocess.run(
        [sys.executable, "-m", "pytest", str(test_path), "-q", "-x", "--tb=short"],
        capture_output=True, text=True,
    )
    return p.returncode == 0, (p.stdout + p.stderr)[-3000:]


def simulate_ui_change(code, site):
    """Pretend developers renamed the login button: rename it inside the test."""
    for el in site["/"]:
        if el.get("type") == "submit" and el.get("id") and el["id"] in code:
            return code.replace(el["id"], el["id"] + "-v2"), el["id"]
    return code, None


# ---------- main ----------
def main():
    start = time.time()
    do_break = "--break" in sys.argv
    offline = "--offline" in sys.argv

    print("[1/4] Crawling the application...")
    site = crawl_site()
    for path, els in site.items():
        print(f"   {path:18} -> {len(els)} elements found")

    results = []
    print("[2/4] Generating and running tests...")
    for n, flow in enumerate(FLOWS):
        path = OUT / f"test_{flow['name']}.py"
        code, source = generate_or_template(flow, site, offline)
        path.write_text(code)
        ok, out = run(path)
        print(f"   {flow['name']}: {'PASS' if ok else 'FAIL'}  (written by: {source})")
        heals, healer = 0, "-"

        if ok and do_break and n == 0:
            code, broken_id = simulate_ui_change(code, site)
            if broken_id:
                print(f"[3/4] SIMULATING UI CHANGE: '{broken_id}' renamed to '{broken_id}-v2'")
                path.write_text(code)
                ok, out = run(path)
                print(f"   {flow['name']} after change: {'PASS' if ok else 'FAIL (test broke)'}")

        while not ok and heals < 2:
            heals += 1
            print(f"   Self-healing {flow['name']} (attempt {heals})...")
            fresh = crawl_site()
            code, healer = heal_or_rules(flow, code, out, fresh, offline)
            path.write_text(code)
            ok, out = run(path)
            print(f"   {flow['name']} after healing: {'PASS' if ok else 'FAIL'}")

        results.append((flow["name"], ok, heals, source, healer))

    print("[4/4] REPORT")
    lines = ["E2E FLOW TEST AGENT - REPORT",
             "Flow map: " + "  ->  ".join(site.keys()), ""]
    for name, ok, heals, source, healer in results:
        lines.append(f"{name:14} {'PASS' if ok else 'FAIL':5}  written by: {source}"
                     f"  | self-heals: {heals} (by {healer})")
    lines.append(f"\nTime taken: {time.time() - start:.0f} seconds")
    report = "\n".join(lines)
    print(report)
    pathlib.Path("report.txt").write_text(report)


if __name__ == "__main__":
    main()