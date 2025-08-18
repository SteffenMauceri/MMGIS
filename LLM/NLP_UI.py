import os
import json
import asyncio
import argparse
from typing import Dict, List, Optional, Tuple
from LLM.config import get_config_value

from LLM.agent_graph import create_agent, summarize_history
from LLM.state import get_memory_store, set_current_thread


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="MMGIS LLM Agent runner")
    parser.add_argument("-m", "--message", type=str, help="Send a single message to the agent and print the response")
    parser.add_argument("-i", "--interactive", action="store_true", help="Start an interactive prompt loop")
    parser.add_argument("--user-id", type=str, default=os.getenv("MMGIS_USER_ID", os.getenv("USER", "anon")))
    parser.add_argument("--session-id", type=str, default=os.getenv("MMGIS_SESSION_ID", "local"))
    parser.add_argument("--mission-id", type=str, default=os.getenv("MMGIS_MISSION_ID"))
    args = parser.parse_args()

    thread: Dict[str, Optional[str]] = {"user_id": args.user_id, "session_id": args.session_id, "mission_id": args.mission_id}
    set_current_thread(thread)
    store = get_memory_store()
    run_id = store.new_run_id()

    # Load prior thread messages (short-term memory)
    prior: List[Tuple[str, str]] = store.load_recent_messages(thread, limit=50)

    agent = create_agent(thread)

    async def run_single(msg: str):
        # Persist user turn
        store.append_messages(thread, [("user", msg)], run_id)
        initial_messages: List[Tuple[str, str]] = prior + [("user", msg)]
        resp = await agent.ainvoke({
            "messages": initial_messages,
            "facts": store.get_facts(thread),
            "brief": store.get_brief(thread),
            "last_ui_state": store.get_last_ui_state(thread),
            "run_id": run_id,
            "thread": thread,
            "scratch": {},
            "step_count": 0,
        })
        # Persist assistant turn (compact)
        store.append_messages(thread, [("assistant", json.dumps(resp, default=str)[:4000])], run_id)
        # Optional summarization
        try:
            flat_messages = prior + [("assistant", json.dumps(resp, default=str)[:1000])]
            if len(flat_messages) >= 40:
                new_brief = await summarize_history(flat_messages, store.get_brief(thread))
                store.set_brief(thread, new_brief)
        except Exception:
            pass
        print(resp)

    async def run_interactive():
        print("Interactive mode. Type 'exit' or Ctrl-D to quit.")
        # Seed in-memory view from durable store
        messages: List[Tuple[str, str]] = prior.copy()
        while True:
            try:
                user_in = input("you> ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                break
            if user_in.lower() in {"exit", "quit"}:
                break
            if not user_in:
                continue

            messages.append(("user", user_in))
            store.append_messages(thread, [("user", user_in)], run_id)

            resp = await agent.ainvoke({
                "messages": messages,
                "facts": store.get_facts(thread),
                "brief": store.get_brief(thread),
                "last_ui_state": store.get_last_ui_state(thread),
                "run_id": run_id,
                "thread": thread,
                "scratch": {},
                "step_count": 0,
            })

            # Persist assistant turn and keep local context concise
            rsp_text = json.dumps(resp, default=str)[:4000]
            messages.append(("assistant", rsp_text))
            store.append_messages(thread, [("assistant", rsp_text)], run_id)

            # Periodically summarize history into brief
            try:
                if len(messages) >= 40:
                    new_brief = await summarize_history(messages, store.get_brief(thread))
                    store.set_brief(thread, new_brief)
                    # Prune to last 10 visible turns to keep loop fast
                    messages = messages[-10:]
            except Exception:
                pass

            print(resp)

    if args.interactive:
        asyncio.run(run_interactive())
    elif args.message:
        asyncio.run(run_single(args.message))
    else:
        async def main():
            # Smoke test 1: list files
            response1 = await agent.ainvoke({
                "messages": prior + [("user", "List all the files in the current directory.")],
                "facts": store.get_facts(thread),
                "brief": store.get_brief(thread),
                "last_ui_state": store.get_last_ui_state(thread),
                "run_id": run_id,
                "thread": thread,
                "scratch": {},
                "step_count": 0,
            })
            print(response1)
            # Smoke test 2: try MMGIS state (works only if MMGIS page is connected over CDP)
            response2 = await agent.ainvoke({
                "messages": prior + [("user", "Get the MMGIS UI state.")],
                "facts": store.get_facts(thread),
                "brief": store.get_brief(thread),
                "last_ui_state": store.get_last_ui_state(thread),
                "run_id": run_id,
                "thread": thread,
                "scratch": {},
                "step_count": 0,
            })
            print(response2)
        asyncio.run(main())

