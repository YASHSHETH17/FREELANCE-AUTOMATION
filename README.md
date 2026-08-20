# Freelance Automation

An AI-powered Gmail assistant for freelancers that can search and read email, draft replies, send manually approved messages, and optionally send a controlled acknowledgement to newly received direct emails.

**Owner:** Yash Sheth

## Features

- Search Gmail messages using Gmail search syntax.
- Read full email content by Gmail message ID.
- Find recent emails with exact minute/hour filtering and optional Primary, Social, Promotions, Updates, or Forums category filtering.
- Draft and send plain-text emails through Gmail.
- Require human approval before every manual send.
- Let the reviewer approve, edit, or reject a proposed outgoing email.
- Prompt for the final email-signature name during human review when the draft contains a name placeholder.
- Run an optional background monitor that acknowledges new direct human emails once per Gmail thread.
- Provide dry-run mode and an audit log for automatic replies.

## Project Structure

```text
FREELANCE-AUTOMATION/
├── src/
│   ├── agent.py          # LangChain agent and HITL middleware configuration
│   ├── auth.py           # Gmail OAuth credential loading and refresh
│   ├── auto_reply.py     # Background automatic-acknowledgement monitor
│   ├── config.py         # Environment configuration
│   ├── gmail_client.py   # Gmail API wrapper
│   ├── main.py           # Interactive terminal application
│   └── tools.py          # Agent tools
├── .env                  # Local secrets and settings; never commit this
├── credentials.json      # Google OAuth client credentials; never commit this
├── token.json            # Gmail OAuth token; never commit this
└── requirements.txt
```

## Setup

### 1. Create and activate a virtual environment

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 2. Configure `.env`

Create a `.env` file in the project root:

```dotenv
# OpenAI-compatible LLM endpoint
LLM_API_KEY=your-api-key
LLM_MODEL_NAME=your-model-name
LLM_BASE_URL=https://your-host/v1

# Automatic reply settings
AUTO_REPLY_ENABLED=false
AUTO_REPLY_DRY_RUN=true
AUTO_REPLY_POLL_SECONDS=60
AUTO_REPLY_MAX_PER_HOUR=10
AUTO_REPLY_BODY=Thank you for your email. I have received your message and will connect with you shortly.
```

### 3. Add Gmail OAuth credentials

Place your Google OAuth Desktop App credentials in the project root as:

```text
credentials.json
```

On the first run, the app opens a browser for Google authorization and creates `token.json`. The configured scopes allow Gmail read access and sending messages.

## Run the Assistant

```bash
python src/main.py
```

Examples:

```text
Who sent my most recent email?
Show recent emails in my Primary inbox from the last hour.
Read the latest email from client@example.com.
Send an email to client@example.com asking for a meeting tomorrow.
```

## Human-in-the-Loop Email Sending

The agent does not send manual emails directly. It first pauses and shows the exact recipient, subject, and body:

```text
--- Email waiting for your approval ---
To:      client@example.com
Subject: Meeting request
Body:
...
------------------------------------------
[a]pprove, [e]dit, or [r]eject:
```

- `a` — sends the reviewed email.
- `e` — lets you change recipient, subject, or body before sending.
- `r` — cancels the send.

If the draft has a signature placeholder such as `[Your Name]`, the HITL flow asks for the name to use, displays the updated draft, and sends that reviewed version only after approval.

## Automatic Acknowledgements

The optional auto-reply monitor runs alongside the terminal assistant. It does not use the LLM to generate outgoing content: it sends only the fixed `AUTO_REPLY_BODY` from `.env`.

### Safety policy

The monitor replies only to new direct-human emails and skips:

- No-reply, postmaster, mailer-daemon, and similar addresses.
- Mailing lists, bulk mail, and messages containing unsubscribe headers.
- Automated messages and auto-response messages.
- Gmail Promotions, Social, Updates, and Forums categories.
- Threads that already received an automatic reply.

It also limits replies to `AUTO_REPLY_MAX_PER_HOUR` and records a startup baseline, so it never replies to messages already present before the monitor starts.

### Test safely with dry-run mode

Use this first:

```dotenv
AUTO_REPLY_ENABLED=true
AUTO_REPLY_DRY_RUN=true
```

Restart the app, then send a **new** email from another account. The terminal logs an `AUTO-REPLY DRY RUN` record but Gmail sends nothing.

Review the history from the running assistant:

```text
auto-replies
```

### Enable live automatic replies

After dry-run testing succeeds, change only:

```dotenv
AUTO_REPLY_DRY_RUN=false
```

Restart the app. It should report:

```text
Automatic replies are enabled for new direct human emails (LIVE).
```

Send a new test email after startup. Previously handled dry-run messages are intentionally not retried.

## Audit Files

Automatic-reply state and audit history are stored locally:

```text
.auto_reply_state.json
auto_reply_log.csv
```

The CSV audit log records delivery metadata such as timestamp, sender, subject, Gmail thread ID, and message ID. It does not store email bodies.

## Security Notes

Never commit sensitive local files. Your `.gitignore` should include:

```gitignore
.env
credentials.json
token.json
.auto_reply_state.json
auto_reply_log.csv
__pycache__/
*.pyc
```

Keep automatic replies in dry-run mode until you have verified the recipient filtering and message text using a separate test account.

## Tech Stack

- Python
- LangChain and LangGraph
- Human-in-the-Loop middleware
- Gmail API with Google OAuth 2.0
- OpenAI-compatible LLM endpoint

## Owner

**Yash Sheth**
