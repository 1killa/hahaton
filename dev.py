"""Explicit standalone UI demo. No real slicing or native print exports."""
import argparse
import os
import threading
import webbrowser

from plugin.AIAssistant.agent.config import ConfigStore
from plugin.AIAssistant.agent.core import AgentSession
from plugin.AIAssistant.agent.server import AssistantServer
from plugin.AIAssistant.agent.slicer import DemoAdapter
from plugin.AIAssistant.agent.tools import ToolRegistry


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--port", type=int, default=0)
    args = parser.parse_args()
    runtime = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".runtime")
    output_dir = os.path.join(runtime, "outputs")
    os.makedirs(output_dir, exist_ok=True)
    config = ConfigStore()
    tools = ToolRegistry(DemoAdapter(output_dir), output_dir)
    session = AgentSession(config, tools)
    server = AssistantServer(session, tools, config, runtime, mode="demo", port=args.port)
    print("DEMO — native slicing/export unavailable. API key is kept in process memory only.")
    print(server.browser_url(), flush=True)
    if not args.no_browser:
        webbrowser.open(server.browser_url())
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        session.cancel()
        server.server_close()
        server.cleanup_uploads()


if __name__ == "__main__":
    main()
