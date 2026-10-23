"""Small JSON-RPC 2.0 / MCP subset, without a modern Python SDK."""
import json


def dispatch_rpc(request, tools, cancel=None):
    if not isinstance(request, dict) or request.get("jsonrpc") != "2.0" or not isinstance(request.get("method"), str):
        return {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "Invalid Request"}}
    request_id = request.get("id")
    notification = "id" not in request
    if isinstance(request_id, (dict, list, bool)):
        return {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "Invalid id"}}
    method = request["method"]
    params = request.get("params", {})
    error = None
    result = None
    if not isinstance(params, dict):
        error = {"code": -32602, "message": "Invalid params"}
    elif method == "initialize":
        result = {"protocolVersion": "2024-11-05", "capabilities": {"tools": {}},
                  "serverInfo": {"name": "STE Assistant", "version": "0.1.0"}}
    elif method == "tools/list":
        result = {"tools": tools.list_tools()}
    elif method == "tools/call":
        if not isinstance(params.get("name"), str) or not isinstance(params.get("arguments", {}), dict):
            error = {"code": -32602, "message": "Expected name and arguments"}
        else:
            try:
                value = tools.call(params["name"], params.get("arguments", {}), cancel=cancel)
                result = {"content": [{"type": "text", "text": json.dumps(value, ensure_ascii=False)}], "isError": False}
            except Exception as exc:
                result = {"content": [{"type": "text", "text": str(exc)}], "isError": True}
    elif method == "notifications/initialized" and notification:
        return None
    else:
        error = {"code": -32601, "message": "Method not found"}
    if notification:
        return None
    response = {"jsonrpc": "2.0", "id": request_id}
    response["error" if error else "result"] = error if error else result
    return response
