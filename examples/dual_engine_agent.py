#!/usr/bin/env python3
"""
Dual-Engine Browser Agent Example: Jev 1.13 + GLM-5.3-Flash

Architecture:
1. Fast Reflex Loop (System 1): TypeSafe Jev 1.13 executes sub-50ms atomic choices.
2. Smart Text Helper: GLM-5.3-Flash generates structured text when TYPE_TEXT is required.
3. Multimodal Supervisor (System 2): GLM-5.3-Flash inspects screenshots when stuck.
"""

import argparse
import os
import time

from jev_ultrafast import Agent, GLMSupervisor


def run_dual_engine(url: str, goal: str, max_supervisor_retries: int = 3):
    print("=" * 65)
    print("⚡ Starting Dual-Engine Ultrafast Browser Agent")
    print(f"🎯 Target URL : {url}")
    print(f"🎯 User Goal   : {goal}")
    print(f"🤖 Action Model: {os.environ.get('TYPESAFE_MODEL', 'jev-latest')}")
    print(f"🧠 Brain Model : {os.environ.get('TEXT_MODEL', 'glm-5.3-flash')} (Text & Vision)")
    print("=" * 65)

    supervisor = GLMSupervisor()
    has_supervisor = supervisor.is_configured()
    if has_supervisor:
        print("👁️  GLM-5.3-Flash Visual Supervisor: [ENABLED]")
    else:
        print("⚠️  GLM-5.3-Flash Visual Supervisor: [DISABLED - No API Key]")

    start_time = time.perf_counter()
    supervisor_interventions = 0

    with Agent(url, goal, screenshots=True) as agent:
        print("\n🚀 Agent loop initiated...")

        while agent.state["status"] not in {"done", "blocked"}:
            agent.command("tick")

            # Realtime action telemetry
            if agent.state["history"]:
                last = agent.state["history"][-1]
                text_info = f" [Typed: '{last['text']}']" if last.get("text") else ""
                print(
                    f"⏱️  [{last['elapsed_ms']}ms] Step {last['step']}: "
                    f"{last['operation']} -> {last['action']}{text_info} "
                    f"(Conf: {last['confidence']:.2f}, Latency: {last['latency_ms']}ms)"
                )

            # Check for deadlock / blocked condition
            if agent.state["status"] == "blocked" and has_supervisor:
                if supervisor_interventions >= max_supervisor_retries:
                    print("🛑 Maximum supervisor retries reached; stopping.")
                    break

                supervisor_interventions += 1
                print(f"\n⚠️  Deadlock detected! Calling GLM Supervisor (#{supervisor_interventions})...")
                screenshot_b64 = agent.state["page"]["screenshot"]
                diagnosis = supervisor.diagnose(
                    screenshot_b64=screenshot_b64,
                    goal=goal,
                    recent_actions=agent.state["history"],
                )
                print(f"🧠 Supervisor Diagnosis: {diagnosis.get('stuck_reason')}")
                print(
                    f"🔧 Recommended Action : {diagnosis.get('action_type')} "
                    f"(Auto-recover: {diagnosis.get('can_auto_recover')})"
                )

                if diagnosis.get("can_auto_recover"):
                    recovered = supervisor.apply_recovery(agent.browser, diagnosis)
                    if recovered:
                        print("✅ Recovery action applied! Refreshing page state and resuming Jev loop...")
                        agent.state["status"] = "ready"
                        agent.state["page"] = agent.browser.observe(screenshot=True)
                        continue
                    else:
                        print("❌ Failed to apply recovery action.")

        # Final outcome handling
        total_time = round((time.perf_counter() - start_time), 2)
        print("\n" + "=" * 65)
        print(f"🏁 Loop Completed with status: [{agent.state['status'].upper()}] in {total_time}s")
        print(f"📊 Total Actions executed: {len(agent.state['history'])}")
        print(f"✍️  Text generation calls : {len(agent.state['text_calls'])}")
        print(f"👁️  Supervisor Interventions: {supervisor_interventions}")

        # Final visual audit if status is done
        if agent.state["status"] == "done" and has_supervisor:
            print("\n🔍 Conducting final visual audit with GLM-5.3-Flash...")
            final_screen = agent.state["page"]["screenshot"]
            audit = supervisor.verify_goal_achievement(final_screen, goal)
            print(f"📋 Visual Audit Result : {'PASS ✅' if audit.get('satisfied') else 'FAIL ❌'}")
            print(f"🔍 Confidence Score    : {audit.get('confidence', 0):.2f}")
            print(f"💬 Explanation         : {audit.get('explanation')}")

        print("=" * 65)


def main():
    default_goal = (
        "Find one-way flights from Zurich to London on September 20, 2026 for one adult in economy. "
        "Stop when matching flight options are visible."
    )
    parser = argparse.ArgumentParser(description="Dual-Engine Jev + GLM-5.3-Flash Agent")
    parser.add_argument(
        "--url",
        default="https://www.google.com/travel/flights?hl=en",
        help="Initial URL to navigate to",
    )
    parser.add_argument(
        "--goal",
        default=default_goal,
        help="Natural language goal",
    )
    args = parser.parse_args()
    run_dual_engine(args.url, args.goal)


if __name__ == "__main__":
    main()
