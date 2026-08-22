# Freelance Automation

An AI-powered assistant for freelancers that can search and read email, draft replies, send manually approved messages, monitor inbox activity, and generate public business leads into a structured Excel workbook through a reusable asynchronous MCP service.

**Owner:** Yash Sheth

## Features

- Search Gmail messages using Gmail search syntax.
- Read full email content by Gmail message ID.
- Find recent emails with exact minute/hour filtering and optional Primary, Social, Promotions, Updates, or Forums category filtering.
- Draft and send plain-text emails through Gmail.
- Require human approval before every manual send.
- Let the reviewer approve, edit, or reject a proposed outgoing email.
- Prompt for the final email-signature name during human review when the draft contains a name placeholder.
- Run an optional background monitor that queues every new Inbox message, reports completed work in the terminal, and acknowledges eligible human emails once per Gmail thread.
- Optionally let the LLM rewrite the approved acknowledgement template without giving it permission to choose recipients or send mail.
- Provide dry-run mode and an audit log for automatic replies.
- Automatically discover public business websites from a profession, service list, and City, State location, then generate leads with contact details, sector, fit signals, evidence URLs, and an AI-assisted approach comment.
- Export lead results to a formatted Excel workbook with Leads, Sources, and Run Summary sheets.
- Expose the lead-generation and low-level scraping tools through a standalone Streamable HTTP MCP server that can serve multiple agents concurrently.

## Project Structure

```text
FREELANCE-AUTOMATION/
├── src/
│   ├── email_agent/
│   │   ├── agent.py          # LangChain agent and HITL middleware configuration
│   │   ├── auth.py           # Gmail OAuth credential loading and refresh
│   │   ├── auto_reply.py     # Background queue, worker, and notifications
│   │   ├── config.py         # Environment configuration
│   │   ├── gmail_client.py   # Gmail API wrapper
│   │   ├── main.py           # Interactive terminal application
│   │   └── tools.py          # Agent tools
│   └── web_scrapper_agent/
│       ├── agent.py          # Async lead-generation agent and Excel export flow
│       ├── client.py         # Async LangChain MCP client adapter
│       ├── config.py         # Scraper, lead, and MCP configuration
│       ├── discovery.py      # Async public business-URL discovery
│       ├── lead_extractor.py # Business fields, fit signals, and evidence extraction
│       ├── lead_models.py    # Lead profile and business-lead data models
│       ├── lead_pipeline.py  # Concurrent candidate discovery and qualification
│       ├── main.py           # Lead-generation terminal application
│       ├── provider.py       # Async HTTP/HTML scraper
│       ├── server.py         # Reusable MCP lead-generation service
│       └── workbook.py        # Formatted Excel workbook export
├── .env                      # Local secrets and settings; never commit this
├── src/email_agent/credentials.json  # OAuth client credentials; never commit this
├── src/email_agent/token.json        # Gmail OAuth token; never commit this
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
LLM_REASONING_EFFORT=none
LLM_MAX_COMPLETION_TOKENS=1024
LLM_TIMEOUT_SECONDS=120

# Automatic reply settings
BACKGROUND_INBOX_ENABLED=false
AUTO_REPLY_ENABLED=false
AUTO_REPLY_DRY_RUN=true
AUTO_REPLY_REWRITE_ENABLED=false
AUTO_REPLY_POLL_SECONDS=60
AUTO_REPLY_MAX_PER_HOUR=10
AUTO_REPLY_BODY=Thank you for your email. I have received your message and will connect with you shortly.

# URL-scraping MCP service
SCRAPER_TIMEOUT_SECONDS=20
SCRAPER_MAX_BYTES=2000000
SCRAPER_MAX_CHARS=12000
SCRAPER_MAX_URLS=10
SCRAPER_MAX_CONCURRENCY=5
SCRAPER_MAX_REDIRECTS=5
SCRAPER_MAX_LINKS=50
SCRAPER_USER_AGENT=FreelanceAutomationBot/1.0
MCP_WEB_SCRAPER_HOST=127.0.0.1
MCP_WEB_SCRAPER_PORT=8000
MCP_WEB_SCRAPER_URL=http://127.0.0.1:8000/mcp

# Lead-generation output and concurrency
LEAD_OUTPUT_DIR=outputs/leads
LEAD_MAX_CANDIDATE_MULTIPLIER=3
LEAD_AI_COMMENT_MAX_CHARS=600
LEAD_AI_COMMENT_CONCURRENCY=3
LEAD_SEARCH_URL=https://html.duckduckgo.com/html/
LEAD_SEARCH_TIMEOUT_SECONDS=20
LEAD_SEARCH_MAX_QUERIES=4
LEAD_SEARCH_RESULTS_PER_QUERY=8
LEAD_SEARCH_CONCURRENCY=3
```

### 3. Add Gmail OAuth credentials

Place your Google OAuth Desktop App credentials at:

```text
src/email_agent/credentials.json
```

On the first run, the app opens a browser for Google authorization and creates `src/email_agent/token.json`. The configured scopes allow Gmail read access and sending messages.

## Run the Assistant

```bash
python -m src.email_agent.main
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

The optional background monitor runs alongside the terminal assistant. It discovers new Inbox messages without requiring a manual “check mail” request, queues them, and prints a bulleted summary after the current terminal task reaches a safe boundary. Enable it with `BACKGROUND_INBOX_ENABLED=true`.

Only eligible direct human messages are sent an automatic acknowledgement when `AUTO_REPLY_ENABLED=true`. By default, the worker sends the fixed `AUTO_REPLY_BODY` from `.env`. If `AUTO_REPLY_REWRITE_ENABLED=true`, the LLM may rewrite that template in a friendly tone; it does not receive Gmail tools, choose recipients, or decide whether a message is eligible.

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
BACKGROUND_INBOX_ENABLED=true
AUTO_REPLY_ENABLED=true
AUTO_REPLY_DRY_RUN=true
```

Restart the app, then send a **new** email from another account. After the background task completes, the terminal prints a bullet containing the sender, subject, timestamp, and whether an auto-reply was sent or skipped. Gmail sends nothing in dry-run mode.

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

## Async Lead-Generation MCP Agent

The lead-generation capability is split into two processes:

1. An MCP server that owns page fetching, HTML parsing, and concurrent lead collection.
2. A LangChain agent that connects to the server asynchronously, asks for the user’s profile, and exports the results.

Start the MCP server in one terminal:

```bash
python -m src.web_scrapper_agent.server
```

Start the web agent in a second terminal:

```bash
python -m src.web_scrapper_agent.main
```

The server binds to `127.0.0.1` and exposes Streamable HTTP at `/mcp`. Its main
tool is `collect_business_leads`; the lower-level `scrape_url`, `scrape_urls`,
and `extract_links` tools remain available for inspection and debugging.
Requests are asynchronous, bounded by timeouts, response-size limits, character
limits, and concurrency limits. Multiple agents can connect to the same service
and process independent lead runs concurrently.

The terminal workflow asks for:

- Your profession, such as `software engineer`.
- Target location strictly in `City, State` format, such as `Pune, Maharashtra`.
- At least one service to sell, plus optional target sectors.
- The maximum number of leads to return.

The MCP service automatically creates location-and-service search queries,
extracts candidate public business URLs from the search results, and then
scrapes those business pages. Search-result links are treated only as
discovery hints; contact details and lead comments are grounded in the linked
business pages.

The generated workbook is saved under `outputs/leads` by default. It includes
business name, address, phone, email, website, sector/domain, fit score,
confidence, evidence URLs, and an AI-assisted comment explaining how to
approach each business. The AI comment is grounded in the scraped page data;
it is not allowed to invent contact details.

Services are mandatory because they define the type of businesses to target.
The location is intentionally strict so search queries and lead scoring use a
consistent city/state pair.

The scraper only accepts public HTTP/HTTPS URLs, blocks local/private/reserved
addresses to reduce SSRF risk, follows a bounded number of redirects, removes
scripts and navigation elements, and treats page text as untrusted content.
It does not log in, submit forms, or access private data. Link discovery is
bounded to public pages returned by the search provider, and JavaScript-heavy
pages are not rendered in this first version; browser rendering can be added as
a separate opt-in capability later.

## Audit Files

Automatic-reply state and audit history are stored locally:

```text
src/email_agent/.auto_reply_state.json
src/email_agent/auto_reply_log.csv
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
venv/
.venv/
```

Keep automatic replies in dry-run mode until you have verified the recipient filtering and message text using a separate test account.

## Tech Stack

- Python
- asyncio with non-blocking background queues
- LangChain and LangGraph
- Human-in-the-Loop middleware
- Gmail API with Google OAuth 2.0
- OpenAI-compatible LLM endpoint
- Model Context Protocol (MCP) with Streamable HTTP
- BeautifulSoup HTML parser
- Keyless HTML search discovery with a configurable provider endpoint
- Async URL scraping over HTTP

## Owner

**Yash Sheth**
