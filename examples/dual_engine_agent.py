#!/usr/bin/env python3
"""
Dual-Engine Browser Agent Example (Hardened Edition)

Architecture:
1. Fast Reflex Loop (System 1): the policy picks one operation and target per observation.
2. Smart Text Helper: the text model generates structured text when TYPE_TEXT is required.
3. Multimodal Supervisor (System 2): the vision model diagnoses obstacles and audits goals.

Systems 1 and 2 run on whatever .env configures; the banner below prints the models in use.
The shipped configuration puts all three on one OpenRouter key. Point TYPESAFE_ENDPOINT at
TypeSafe's choice API with TYPESAFE_MODEL=jev-latest to run Jev itself as System 1.
"""

import argparse
import logging
import os
import sys
import time

from jev_ultrafast import Agent, GLMSupervisor
from jev_ultrafast.model import DEFAULT_TEXT_MODEL, DEFAULT_TYPESAFE_MODEL

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("dual_engine_agent")


def run_dual_engine(
    url: str,
    goal: str,
    max_steps: int = 60,
    deadline_seconds: int = 300,
    max_supervisor_retries: int = 3,
):
    print("=" * 65)
    print("⚡ Starting Dual-Engine Ultrafast Browser Agent")
    print(f"🎯 Target URL : {url}")
    print(f"🎯 User Goal   : {goal}")
    print(f"🤖 Action Model: {os.environ.get('TYPESAFE_MODEL') or DEFAULT_TYPESAFE_MODEL}")
    print(f"🧠 Brain Model : {os.environ.get('TEXT_MODEL') or DEFAULT_TEXT_MODEL} (Text & Vision)")
    print(f"⏱️  Budget     : Max {max_steps} steps | Deadline {deadline_seconds}s")
    print("=" * 65)

    start_time = time.perf_counter()
    deadline = start_time + deadline_seconds
    supervisor_interventions = 0

    with GLMSupervisor() as supervisor:
        has_supervisor = supervisor.is_configured()
        if has_supervisor:
            print("👁️  GLM-5.3-Flash Visual Supervisor: [ENABLED]")
        else:
            print("⚠️  GLM-5.3-Flash Visual Supervisor: [DISABLED - No API Key or disabled]")

        with Agent(url, goal, screenshots=True) as agent:
            print("\n🚀 Agent loop initiated...")

            # A blocked run is not the end of the loop: the supervisor gets its retries first,
            # so the exit conditions are checked here rather than in the while clause.
            while True:
                if agent.state.get("status") == "done":
                    break
                if time.perf_counter() >= deadline:
                    print(f"\n🛑 Reached execution deadline ({deadline_seconds}s); stopping.")
                    break

                if agent.state.get("status") != "blocked":
                    if len(agent.state.get("history", [])) >= max_steps:
                        print(f"\n🛑 Reached maximum step limit ({max_steps}); stopping.")
                        break

                    # The agent raises on budget exhaustion, provider errors, and text-helper
                    # failures. Surface them as a loop failure instead of an uncaught traceback.
                    try:
                        agent.command("tick")
                    except (ValueError, RuntimeError) as error:
                        print(f"\n🛑 Loop aborted: {error}")
                        break

                    # Realtime action telemetry with safe get() access
                    history = agent.state.get("history", [])
                    if history:
                        last = history[-1]
                        text_val = last.get("text")
                        text_info = f" [Typed: '{text_val}']" if text_val else ""
                        confidence = last.get("confidence") or 0.0
                        print(
                            f"⏱️  [{last.get('elapsed_ms', 0)}ms] Step {last.get('step', len(history))}: "
                            f"{last.get('operation', 'ACTION')} -> {last.get('action', 'unknown')}{text_info} "
                            f"(Conf: {confidence:.2f}, Latency: {last.get('latency_ms', 0)}ms)"
                        )

                    if agent.state.get("status") != "blocked":
                        continue

                # Deadlock. Without a supervisor there is nothing left to try.
                if not has_supervisor:
                    break
                if supervisor_interventions >= max_supervisor_retries:
                    print("🛑 Maximum supervisor retries reached; stopping.")
                    break

                supervisor_interventions += 1
                print(
                    f"\n⚠️  Deadlock detected! Calling GLM Supervisor "
                    f"(#{supervisor_interventions}/{max_supervisor_retries})..."
                )

                # Fresh observation before diagnosis (avoiding stale screenshot)
                fresh_page = agent.browser.observe(screenshot=True)
                screenshot_b64 = fresh_page["screenshot"]

                diagnosis = supervisor.diagnose(
                    screenshot_b64=screenshot_b64,
                    goal=goal,
                    recent_actions=agent.state.get("history", []),
                )
                stuck_reason = diagnosis.get("stuck_reason", "Unknown obstacle")
                action_type = diagnosis.get("action_type", "HUMAN_INTERVENTION")
                can_recover = diagnosis.get("can_auto_recover", False)

                print(f"🧠 Supervisor Diagnosis: {stuck_reason}")
                print(f"🔧 Recommended Action : {action_type} (Auto-recover: {can_recover})")

                if diagnosis.get("rejected_action_type"):
                    print(
                        f"🛡️  SAFETY ALERT: LLM recommendation '{diagnosis.get('rejected_action_type')}' "
                        f"(target: '{diagnosis.get('rejected_target_text')}') was REJECTED by security policy."
                    )

                recovered = False
                if can_recover:
                    recovered = supervisor.apply_recovery(agent.browser, diagnosis)

                if recovered:
                    print("✅ Recovery action applied! Resuming agent loop and injecting feedback for Jev...")
                    agent.resume(
                        reason=stuck_reason,
                        action_type=action_type,
                        note=diagnosis.get("target_text") or diagnosis.get("key_name") or "",
                    )
                    continue

                print("❌ Recovery could not be automatically applied or was rejected by safety policy.")
                if supervisor_interventions >= max_supervisor_retries:
                    break
                # Still blocked. The page may settle, so re-diagnose until the retry cap.
                time.sleep(0.5)

            # Final outcome handling
            total_time = round((time.perf_counter() - start_time), 2)
            final_status = agent.state.get("status", "unknown")
            print("\n" + "=" * 65)
            print(f"🏁 Loop Completed with status: [{final_status.upper()}] in {total_time}s")
            print(f"📊 Total Actions executed: {len(agent.state.get('history', []))}")
            print(f"✍️  Text generation calls : {len(agent.state.get('text_calls', []))}")
            print(f"👁️  Supervisor Interventions: {supervisor_interventions}")

            # Non-done final status (blocked, timeout, or aborted)
            if final_status != "done":
                print(f"🛑 Task did not reach 'done' status (final status: '{final_status}').")
                print("=" * 65)
                sys.exit(2)

            # Final visual audit if status is done (Fail-closed)
            if not has_supervisor:
                print("⚠️  Task marked done by Jev, but Visual Supervisor is disabled; audit UNAVAILABLE.")
                print("🛑 Exiting with code 3 (unverified completion).")
                print("=" * 65)
                sys.exit(3)

            print("\n🔍 Conducting final visual audit with GLM-5.3-Flash...")
            final_fresh = agent.browser.observe(screenshot=True)
            final_screen = final_fresh["screenshot"]
            audit = supervisor.verify_goal_achievement(final_screen, goal)

            satisfied = audit.get("satisfied")
            verdict = {True: "PASS ✅", False: "FAIL ❌"}.get(satisfied, "UNVERIFIED ⚠️")
            print(f"📋 Visual Audit Result : {verdict}")
            print(f"🔍 Confidence Score    : {audit.get('confidence', 0.0):.2f}")
            print(f"💬 Explanation         : {audit.get('explanation')}")
            print("=" * 65)

            if satisfied is not True:
                print("❌ Goal was not visibly verified; exiting with code 1.")
                sys.exit(1)

            print("🎉 Task completed and visually verified successfully!")
            sys.exit(0)


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
    parser.add_argument(
        "--max-steps",
        type=int,
        default=60,
        help="Maximum action steps before termination",
    )
    parser.add_argument(
        "--deadline-seconds",
        type=int,
        default=300,
        help="Maximum execution seconds before timeout",
    )
    args = parser.parse_args()
    run_dual_engine(
        url=args.url,
        goal=args.goal,
        max_steps=args.max_steps,
        deadline_seconds=args.deadline_seconds,
    )


if __name__ == "__main__":
    main()
