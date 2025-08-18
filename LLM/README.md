## MMGIS LLM Quick Start

This folder contains the LLM-driven agent that can control the MMGIS web UI via Playwright. It supports two browser modes:

- **managed**: Playwright launches Chromium for you (default, most stable)
- **attach**: attach to a pre-running Chrome over the Chrome DevTools Protocol (CDP). Useful for demos to keep a window open.


### Prerequisites
- Python 3.10+ (project uses a conda env named `sea_ice`)
- MMGIS running locally on `http://localhost:8888` and API on `http://localhost:8889`
- For browser control: Playwright and Chromium runtime


### Environment setup
Use the project’s conda env:

```bash
source LLM/activate.sh
```

Install Python dependencies for the LLM layer (in the same env):

```bash
pip install -U playwright pytest requests langchain-openai langgraph
python -m playwright install chromium
```


### Running MMGIS with Colima
If you use Colima instead of Docker Desktop, the commands are the same once Colima is running.

```bash
# Start Colima and switch docker context
colima start --cpu 4 --memory 8
docker context use colima

# From the repo root, prepare env files
cp docker-compose.sample.yml docker-compose.yml
cp sample.env .env

# In .env, set DB_USER and DB_PASS
# In docker-compose.yml, set db service POSTGRES_PASSWORD to the same DB_PASS value

# Launch services
docker compose up -d

# Verify UI
curl -k http://localhost:8888/api/utils/healthcheck
```


### Configuration
LLM settings live in `LLM/config.json` and can be overridden by environment variables. Key fields:

- **llm.base_url / llm.model / llm.api_key**: your local or remote LLM endpoint (defaults target Ollama-like compat)
- **browser.mode**: `managed` or `attach` (default `managed`)
- **browser.ui_url**: where to open/connect the UI (default `http://localhost:8888`)
- **browser.headless**: run Chromium headless in managed mode
- **browser.user_data_dir**: persistent profile path (managed persistent or attach launch)
- **browser.keep_open**: keep the window alive between runs; prefers attach-mode
- **browser.cdp_url**: CDP endpoint when attaching (default `http://localhost:9222`)
- **browser.wait_for_networkidle / post_nav_wait_ms / timeouts**: extra waits to ensure MMGIS UI is ready
- **mmgis_api.base_url**: backend API (default `http://localhost:8889`)

Environment variable override examples:

```bash
export MMGIS_BROWSER_MODE=attach
export MMGIS_BROWSER_KEEP_OPEN=true
export MMGIS_BROWSER_USER_DATA_DIR="/Users/<you>/mmgis-playwright-profile"
export MMGIS_CDP_URL="http://localhost:9222"
```


### Browser control: managed vs attach
- **Managed (default)**
  - Playwright launches Chromium. Most reliable for tests/automation.
  - Configure with: `browser.mode=managed`, optional `headless`, optional `user_data_dir` (persistent context).

- **Attach**
  - Attach to a pre-running Chrome started with `--remote-debugging-port`.
  - Recommended for demos when you want the window to stay open.
  - Either start Chrome yourself, or use the helper:

```bash
python LLM/launch_demo_browser.py
```

This launches Chrome detached with a persistent profile and opens `browser.ui_url`. The agent will then attach to it.


### Running the agent and tests
- Smoke test a single agent flow:

```bash
python LLM/tests/test_agent_prompt_location_layer.py
```

- Full test suite (requires `pytest`):

```bash
pytest -q
```

If MMGIS is not up yet or the browser is not reachable, the tests will print a friendly SKIP message.


### Common issues
- **CDP attach fails**: ensure Chrome is started with `--remote-debugging-port=9222` and `browser.cdp_url` matches; prefer `localhost` binding.
- **UI not ready**: increase `browser.post_nav_wait_ms` or enable `browser.wait_for_networkidle`.
- **Chromium missing**: run `python -m playwright install chromium` in your conda env.
- **Ports**: UI `8888`, API `8889` must be accessible from your host.


### Security notes
- When using CDP, keep it bound to `localhost` and do not expose the debugging port to untrusted networks.


### Where to look in code
- `LLM/browser.py`: unified connection logic and waits
- `LLM/tools.py`: tools the agent can execute against the UI and backend
- `LLM/agent_graph.py`: planner + API agent orchestration
- `LLM/config.json`: runtime configuration with ENV overrides
- `LLM/launch_demo_browser.py`: convenience launcher for demo Chrome



