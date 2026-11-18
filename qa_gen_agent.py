"""QA Gen-Agent prototype.

Usage:  python qa_gen_agent.py shopping.py
Steps:  read code -> AI writes unit tests -> run tests + coverage
        -> if tests fail, send the error back to the AI to fix -> report
"""
import os
import pathlib
import re
import subprocess
import sys
import time

from google import genai


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
MAX_ATTEMPTS = 3
client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))


# If the main model is busy (503/429), wait, retry and switch to these backups.
FALLBACK_MODELS = ["gemini-2.5-flash", "gemini-2.5-flash-lite"]
BUSY_SIGNS = ("503", "429", "500", "UNAVAILABLE", "RESOURCE_EXHAUSTED", "NOT_FOUND", "404")


def generate_with_retry(prompt, tries=7):
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


def run_tests(module, test_file):
    result = subprocess.run(
        [sys.executable, "-m", "pytest", test_file,
         f"--cov={module}", "--cov-report=term-missing", "-q"],
        capture_output=True, text=True,
    )
    return result.returncode == 0, result.stdout + result.stderr


def main(source_file):
    start = time.time()
    src = pathlib.Path(source_file)
    module = src.stem
    code = src.read_text()
    test_file = f"test_{module}.py"

    print(f"[1/3] Reading {src.name} and asking AI to write unit tests...")
    tests = ask_ai(
        f"Write pytest unit tests for the Python module `{module}` below. "
        "Cover normal cases, edge cases and error cases (use pytest.raises). "
        f"Start with `from {module} import *`. Return only one python code block.\n\n{code}"
    )

    passed = False
    for attempt in range(1, MAX_ATTEMPTS + 1):
        pathlib.Path(test_file).write_text(tests)
        print(f"[2/3] Running tests (attempt {attempt})...")
        passed, output = run_tests(module, test_file)
        print(output)
        if passed:
            break
        print("Tests failed. Sending the error back to the AI to fix...")
        tests = ask_ai(
            f"These pytest tests failed.\n\nSOURCE:\n{code}\n\nTESTS:\n{tests}\n\n"
            f"ERROR OUTPUT:\n{output}\n\nFix the tests so they are correct for the "
            "source code. Return only one python code block."
        )

    print("[3/3] REPORT")
    print(f"  Source file : {src.name}")
    print(f"  Test file   : {test_file}")
    print(f"  Result      : {'ALL TESTS PASSED' if passed else 'STILL FAILING - needs human review'}")
    print(f"  Attempts    : {attempt}")
    print(f"  Time taken  : {time.time() - start:.1f} seconds")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("Usage: python qa_gen_agent.py <source_file.py>")
    main(sys.argv[1])