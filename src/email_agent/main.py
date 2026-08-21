from __future__ import annotations

import asyncio
import logging
import re
from concurrent.futures import ThreadPoolExecutor
from typing import Any
from uuid import uuid4

from langgraph.types import Command

from src.email_agent.agent import build_email_agent
from src.email_agent.auto_reply import AutoReplyMonitor, recent_auto_replies
from src.email_agent.config import (
    AUTO_REPLY_DRY_RUN,
    AUTO_REPLY_ENABLED,
    BACKGROUND_INBOX_ENABLED,
)

logger = logging.getLogger(__name__)


async def _ainput(prompt: str = "") -> str:
    """Read terminal input without blocking the asyncio event loop."""
    return (await asyncio.to_thread(input, prompt)).strip()


def _latest_assistant_text(result: dict[str, Any]) -> str:
    """Return the final natural-language assistant response, if there is one."""
    for message in reversed(result.get("messages", [])):
        if getattr(message, "type", None) == "ai" and not getattr(
            message, "tool_calls", []
        ):
            return str(message.content).strip()
    return ""


def _interrupt_actions(result: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract HITL action requests from LangGraph's interrupt payload."""
    actions: list[dict[str, Any]] = []
    for interrupt in result.get("__interrupt__", []):
        value = getattr(interrupt, "value", interrupt)
        if isinstance(value, dict):
            actions.extend(value.get("action_requests", []))
    return actions


SIGNATURE_PLACEHOLDER = re.compile(
    r"\[\s*(?:your|sender)(?:\s+\w+){0,2}\s+name\s*\]",
    flags=re.IGNORECASE,
)


async def apply_signature_name(body: str) -> tuple[str, bool]:
    """Ask for a name only when the model used a signature placeholder."""
    if not SIGNATURE_PLACEHOLDER.search(body):
        return body, False

    while True:
        sender_name = await _ainput("Name to use in the email signature: ")
        if sender_name:
            updated_body = SIGNATURE_PLACEHOLDER.sub(sender_name, body)
            return updated_body, True
        print("Please enter a name.")


async def _review_email(action: dict[str, Any]) -> dict[str, Any]:
    """Show the exact pending email and return one HITL decision."""
    args = dict(action.get("args", {}))
    args["body"], signature_was_changed = await apply_signature_name(
        str(args.get("body", ""))
    )
    print("\n--- Email waiting for your approval ---")
    print(f"To:      {args.get('to', '')}")
    print(f"Subject: {args.get('subject', '')}")
    print("Body:")
    print(args.get("body", ""))
    print("------------------------------------------")

    while True:
        choice = (await _ainput("[a]pprove, [e]dit, or [r]eject: ")).lower()
        if choice in {"a", "approve"}:
            if signature_was_changed:
                return {
                    "type": "edit",
                    "edited_action": {
                        "name": action["name"],
                        "args": args,
                    },
                }
            return {"type": "approve"}

        if choice in {"r", "reject"}:
            feedback = await _ainput("Optional rejection reason: ")
            return {"type": "reject", "message": feedback or "User rejected this email."}

        if choice in {"e", "edit"}:
            print("Press Enter to keep the displayed value.")
            to = await _ainput(f"To [{args.get('to', '')}]: ") or args.get("to", "")
            subject = (
                await _ainput(f"Subject [{args.get('subject', '')}]: ")
                or args.get("subject", "")
            )
            print("Body (replace the full body; finish with one line containing only '.'):")
            lines: list[str] = []
            while True:
                line = await _ainput()
                if line == ".":
                    break
                lines.append(line)
            body = "\n".join(lines).strip() or args.get("body", "")
            body, _ = await apply_signature_name(body)
            return {
                "type": "edit",
                "edited_action": {
                    "name": action["name"],
                    "args": {"to": to, "subject": subject, "body": body},
                },
            }

        print("Please enter a, e, or r.")


async def _handle_interrupt(
    agent: Any,
    result: dict[str, Any],
    config: dict[str, Any],
) -> dict[str, Any]:
    actions = _interrupt_actions(result)
    if not actions:
        print("The agent paused, but no reviewable action was provided.")
        return result

    decisions = [await _review_email(action) for action in actions]
    return await agent.ainvoke(Command(resume={"decisions": decisions}), config=config)


async def _print_auto_reply_history() -> None:
    records = await recent_auto_replies()
    if not records:
        print("\nNo automatic replies have been sent yet.\n")
        return
    print("\n--- Most recent automatic replies ---")
    for record in records:
        status = record.get("status", "sent")
        print(
            f"{record['sent_at']} | {status.upper()} | "
            f"To: {record['recipient']} | Subject: {record['subject']}"
        )
    print("-------------------------------------\n")


async def _print_background_notifications(
    monitor: AutoReplyMonitor | None,
) -> None:
    if not monitor:
        return
    notifications = await monitor.drain_notifications()
    if not notifications:
        return

    print("\n--- New inbox activity ---")
    for notification in notifications:
        print(notification.display_line())
    print("--------------------------\n")


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )
    agent = await asyncio.to_thread(build_email_agent)
    config = {"configurable": {"thread_id": f"terminal-{uuid4()}"}}
    monitor = AutoReplyMonitor() if BACKGROUND_INBOX_ENABLED else None
    if monitor:
        await monitor.start()
        if AUTO_REPLY_ENABLED:
            mode = "DRY RUN — no email will be sent" if AUTO_REPLY_DRY_RUN else "LIVE"
            print(
                "Background inbox monitor is enabled; "
                f"automatic replies are enabled ({mode})."
            )
        else:
            print("Background inbox monitor is enabled; automatic replies are disabled.")
    else:
        print(
            "Background inbox monitor is disabled. "
            "Set BACKGROUND_INBOX_ENABLED=true to enable it."
        )
    print("Email assistant ready. Sending email always requires your approval.")
    print("Type 'auto-replies' to view automatic-reply history. Type 'quit' to exit.\n")

    try:
        while True:
            await _print_background_notifications(monitor)
            try:
                user_input = await _ainput("You: ")
            except (EOFError, KeyboardInterrupt):
                break
            if not user_input:
                continue
            if user_input.lower() in {"quit", "exit"}:
                break
            if user_input.lower() in {"auto-replies", "/auto-replies"}:
                await _print_auto_reply_history()
                continue

            try:
                result = await agent.ainvoke(
                    {"messages": [{"role": "user", "content": user_input}]},
                    config=config,
                )
                while _interrupt_actions(result):
                    result = await _handle_interrupt(agent, result, config)
            except Exception as error:
                logger.exception("Email-agent task failed.")
                print(f"\nAssistant task failed: {error}\n")
                continue

            response = _latest_assistant_text(result)
            if response:
                print(f"\nAssistant: {response}\n")
            await _print_background_notifications(monitor)
    finally:
        if monitor:
            await monitor.stop()


def run() -> None:
    """Run the async application with an explicit executor lifecycle."""
    loop = asyncio.new_event_loop()
    executor = ThreadPoolExecutor(max_workers=8, thread_name_prefix="email-io")
    loop.set_default_executor(executor)
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(main())
    finally:
        pending = asyncio.all_tasks(loop)
        for task in pending:
            task.cancel()
        if pending:
            loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
        loop.run_until_complete(loop.shutdown_asyncgens())
        executor.shutdown(wait=False, cancel_futures=True)
        loop.close()
        asyncio.set_event_loop(None)


if __name__ == "__main__":
    run()
