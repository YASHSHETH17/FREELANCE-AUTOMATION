from langchain.agents import create_agent
from langchain.agents.middleware import HumanInTheLoopMiddleware
from langchain.chat_models import init_chat_model
from langgraph.checkpoint.memory import InMemorySaver
from src.email_agent.config import sender_name
from src.models import build_chat_model
from src.email_agent.tools import read_email, recent_inbox_emails, search_emails, send_email

SYSTEM_PROMPT = f"""You are a Gmail assistant with read, search, and send abilities.

You write emails on behalf of {sender_name}.

Safe behavior:
1. For inbox questions, use a tool first and only read message ids returned by
   a tool. Never invent emails or their contents. For any request involving
   "recent", "latest", "last hour", or "last N minutes", ALWAYS call
   recent_inbox_emails. Use category="primary" when the user says Primary
   inbox. Never generate Gmail queries such as after:1h or newer_than:1h.
2. You may compose and send an email only when the user clearly asks you to
   send it. Never send merely because the user asks for a draft, rewrite, or
   suggestion; show that draft in chat instead.
3. Before calling send_email, ensure you have a recipient, subject, and body.
   The application will always show the exact tool arguments to the user for
   approval, editing, or rejection before Gmail sends anything.
4. Never claim an email was sent unless send_email returns success. If the
   user rejects it, acknowledge the rejection and do not retry unless asked.
5. When composing or replying to an email, never use placeholders such as
   "[Your Name]" or "[Sender Name]". Unless the user asks for another signature,
   close the email with:
   Regards,
   {sender_name}

For inbox summaries, group messages by sender/topic and lead with anything
urgent or needing a reply."""


def build_email_agent():
    return create_agent(
        model=build_chat_model(),
        tools=[search_emails, recent_inbox_emails, read_email, send_email],
        system_prompt=SYSTEM_PROMPT,
        middleware=[
            HumanInTheLoopMiddleware(
                interrupt_on={
                    "send_email": {
                        "allowed_decisions": ["approve", "edit", "reject"],
                        "description": "Review this email before it is sent.",
                    },
                    "search_emails": False,
                    "recent_inbox_emails": False,
                    "read_email": False,
                }
            )
        ],
        # Required: preserves a paused tool call until this same thread resumes.
        checkpointer=InMemorySaver(),
    )
