"""Simple UI for the E2E Flow Test Agent.

Run:  streamlit run app.py      (from the same folder as e2e_agent.py and .env)
"""
import difflib
import os
import pathlib
import time

import streamlit as st

st.set_page_config(page_title="E2E Flow Test Agent", page_icon="🧪", layout="wide")

try:
    import e2e_agent as agent
except Exception as err:  # noqa: BLE001
    st.error(f"Could not load e2e_agent.py: {err}")
    st.info("Make sure app.py, e2e_agent.py and .env are in the same folder, "
            "and that GEMINI_API_KEY is set in .env")
    st.stop()

st.title("🧪 E2E Flow Test Agent")
st.caption("An AI agent that discovers user flows, writes Selenium tests, runs them, "
           "and repairs them automatically when the UI changes.")

# ---------------- sidebar ----------------
with st.sidebar:
    st.header("Settings")
    st.text_input("Application under test", agent.BASE, disabled=True)
    use_ai = st.checkbox("Use Gemini AI to write / fix tests", value=True,
                         help="If the AI is busy, the agent falls back to built-in "
                              "templates and a rule-based healer automatically.")
    simulate = st.checkbox("Simulate a UI change (self-healing demo)", value=True,
                           help="Renames the login button's ID inside the test, "
                                "as if developers had changed the page.")
    show_browser = st.checkbox("Show browser window while testing", value=False)
    st.divider()
    st.subheader("Flows tested")
    for f in agent.FLOWS:
        st.markdown(f"**{f['name']}**  \n{f['goal']}")


def show_diff(old, new):
    diff = "\n".join(difflib.unified_diff(
        old.splitlines(), new.splitlines(), "before healing", "after healing", lineterm="", n=0))
    return diff or "(no textual change)"


def run_agent(offline, simulate, show_browser):
    os.environ["HEADLESS"] = "0" if show_browser else "1"
    agent.AI_DOWN = False
    start = time.time()
    results = []

    # ---- step 1: crawl ----
    with st.status("Step 1: Crawling the application...", expanded=True) as status:
        site = agent.crawl_site()
        st.table([{"Page": p, "Elements discovered": len(els)} for p, els in site.items()])
        status.update(label="Step 1: Application crawled ✅", state="complete")

    st.subheader("Discovered flow map")
    labels = ["Login page", "Products page", "Cart page"]
    chain = " -> ".join(f'"{labels[i] if i < len(labels) else p}"' for i, p in enumerate(site))
    st.graphviz_chart(f"digraph {{ rankdir=LR; node [shape=box, style=rounded]; {chain} }}")

    # ---- step 2: each flow ----
    st.subheader("Test generation, execution and self-healing")
    for n, flow in enumerate(agent.FLOWS):
        path = agent.OUT / f"test_{flow['name']}.py"
        failed_output = None
        healing_diffs = []

        with st.status(f"Flow: {flow['name']}", expanded=True) as status:
            st.write(f"**Goal:** {flow['goal']}")
            try:
                code, source = agent.generate_or_template(flow, site, offline)
            except Exception as err:  # noqa: BLE001
                st.error(str(err))
                status.update(label=f"Flow: {flow['name']} ❌", state="error")
                results.append((flow["name"], False, 0, "none", "-"))
                continue
            first_code = code
            path.write_text(code)
            st.write(f"Test written by: **{source}**")
            ok, out = agent.run(path)
            st.write("First run: " + ("✅ PASS" if ok else "❌ FAIL"))
            heals, healer = 0, "-"

            if ok and simulate and n == 0:
                broken, broken_id = agent.simulate_ui_change(code, site)
                if broken_id:
                    st.warning(f"UI change simulated: element id `{broken_id}` was renamed "
                               f"to `{broken_id}-v2`")
                    code = broken
                    path.write_text(code)
                    ok, out = agent.run(path)
                    st.write("After UI change: " + ("✅ PASS" if ok else "❌ FAIL (the test broke)"))

            while not ok and heals < 2:
                heals += 1
                st.info(f"🔧 Self-healing, attempt {heals}: re-reading the live page...")
                fresh = agent.crawl_site()
                new_code, healer = agent.heal_or_rules(flow, code, out, fresh, offline)
                healing_diffs.append(show_diff(code, new_code))
                code = new_code
                path.write_text(code)
                ok, out = agent.run(path)
                st.write(f"Healed by **{healer}** -> " + ("✅ PASS" if ok else "❌ still failing"))

            if not ok:
                failed_output = out
            status.update(label=f"Flow: {flow['name']} " + ("✅" if ok else "❌"),
                          state="complete" if ok else "error")

        # expanders must live outside st.status (Streamlit does not allow nesting)
        for d in healing_diffs:
            with st.expander(f"What the self-healing changed ({flow['name']})"):
                st.code(d, language="diff")
        with st.expander(f"Generated test code ({flow['name']})"):
            st.code(code, language="python")
        if failed_output:
            with st.expander(f"Error output ({flow['name']})"):
                st.code(failed_output)

        results.append((flow["name"], ok, heals, source, healer))

    # ---- summary ----
    elapsed = time.time() - start
    st.subheader("Summary")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Flows passed", f"{sum(1 for r in results if r[1])} / {len(results)}")
    c2.metric("Self-heals used", sum(r[2] for r in results))
    c3.metric("Pages discovered", len(site))
    c4.metric("Time taken", f"{elapsed:.0f} s")
    st.table([{"Flow": r[0], "Result": "PASS" if r[1] else "FAIL", "Written by": r[3],
               "Self-heals": r[2], "Healed by": r[4]} for r in results])

    lines = ["E2E FLOW TEST AGENT - REPORT", "Flow map: " + "  ->  ".join(site.keys()), ""]
    for name, ok, heals, source, healer in results:
        lines.append(f"{name:14} {'PASS' if ok else 'FAIL':5}  written by: {source}"
                     f"  | self-heals: {heals} (by {healer})")
    lines.append(f"\nTime taken: {elapsed:.0f} seconds")
    report = "\n".join(lines)
    pathlib.Path("report.txt").write_text(report)
    st.download_button("Download report.txt", report, file_name="report.txt")


st.write("Click the button to let the agent crawl the app, write the tests, run them and "
         "heal anything that breaks.")
if st.button("▶ Run agent", type="primary"):
    run_agent(offline=not use_ai, simulate=simulate, show_browser=show_browser)