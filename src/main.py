from __future__ import annotations
import re
import logging
from typing import Any
from uuid import uuid4

from langgraph.types import Command

from agent import build_email_agent
from auto_reply import AutoReplyMonitor, recent_auto_replies
from config import AUTO_REPLY_DRY_RUN, AUTO_REPLY_ENABLED


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


def apply_signature_name(body: str) -> tuple[str, bool]:
    """Ask for a name only when the model used a signature placeholder."""

    if not SIGNATURE_PLACEHOLDER.search(body):
        return body, False

    while True:
        sender_name = input(
            "Name to use in the email signature: "
        ).strip()

        if sender_name:
            updated_body = SIGNATURE_PLACEHOLDER.sub(
                sender_name,
                body,
            )
            return updated_body, True

        print("Please enter a name.")

def _review_email(action: dict[str, Any]) -> dict[str, Any]:
    """Show the exact pending email and return one HITL decision."""
    args = dict(action.get("args", {}))
    args["body"], signature_was_changed = apply_signature_name(
    str(args.get("body", ""))
    )
    print("\n--- Email waiting for your approval ---")
    print(f"To:      {args.get('to', '')}")
    print(f"Subject: {args.get('subject', '')}")
    print("Body:")
    print(args.get("body", ""))
    print("------------------------------------------")

    while True:
        choice = input("[a]pprove, [e]dit, or [r]eject: ").strip().lower()
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
            feedback = input("Optional rejection reason: ").strip()
            return {"type": "reject", "message": feedback or "User rejected this email."}
        if choice in {"e", "edit"}:
            print("Press Enter to keep the displayed value.")
            to = input(f"To [{args.get('to', '')}]: ").strip() or args.get("to", "")
            subject = (
                input(f"Subject [{args.get('subject', '')}]: ").strip()
                or args.get("subject", "")
            )
            print("Body (replace the full body; finish with one line containing only '.'):")
            lines: list[str] = []
            while True:
                line = input()
                if line == ".":
                    break
                lines.append(line)
            body = "\n".join(lines).strip() or args.get("body", "")
            body, _ = apply_signature_name(body)
            return {
                "type": "edit",
                "edited_action": {"name": action["name"], "args": {"to": to, "subject": subject, "body": body}},
            }
        print("Please enter a, e, or r.")


def _handle_interrupt(agent: Any, result: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    actions = _interrupt_actions(result)
    if not actions:
        print("The agent paused, but no reviewable action was provided.")
        return result

    # Decisions must be supplied in exactly the same order as action_requests.
    decisions = [_review_email(action) for action in actions]
    return agent.invoke(Command(resume={"decisions": decisions}), config=config)


def _print_auto_reply_history() -> None:
    records = recent_auto_replies()
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


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )
    agent = build_email_agent()
    config = {"configurable": {"thread_id": f"terminal-{uuid4()}"}}
    monitor = AutoReplyMonitor() if AUTO_REPLY_ENABLED else None
    if monitor:
        monitor.start()
        mode = "DRY RUN — no email will be sent" if AUTO_REPLY_DRY_RUN else "LIVE"
        print(f"Automatic replies are enabled for new direct human emails ({mode}).")
    else:
        print("Automatic replies are disabled. Set AUTO_REPLY_ENABLED=true in .env to enable them.")
    print("Email assistant ready. Sending email always requires your approval.")
    print("Type 'auto-replies' to view automatic-reply history. Type 'quit' to exit.\n")

    try:
        while True:
            try:
                user_input = input("You: ").strip()
            except (EOFError, KeyboardInterrupt):
                break
            if not user_input:
                continue
            if user_input.lower() in {"quit", "exit"}:
                break
            if user_input.lower() in {"auto-replies", "/auto-replies"}:
                _print_auto_reply_history()
                continue

            result = agent.invoke(
                {"messages": [{"role": "user", "content": user_input}]},
                config=config,
            )
            if _interrupt_actions(result):
                result = _handle_interrupt(agent, result, config)

            response = _latest_assistant_text(result)
            if response:
                print(f"\nAssistant: {response}\n")
    finally:
        if monitor:
            monitor.stop()


if __name__ == "__main__":
    main()
