import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch

from mcp_autogui.smoke import _decode_mcp_response, _mcp_describe_check, _treeland_tree_check, main


def test_smoke_reports_effective_config_and_tree_check(capsys):
    payload = {
        "schema_version": 2,
        "transport": {
            "mode": "streamable-http", "host": "127.0.0.1", "port": 8651,
            "auth": {"mode": "loopback"},
        },
        "desktop_backend": {"kind": "treeland-deepin"},
        "proposal_provider": {"kind": "qwen-cua", "base_url": "https://model.example/v1"},
    }
    with TemporaryDirectory() as directory:
        path = Path(directory) / "config.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        with (
            patch("mcp_autogui.smoke._treeland_tree_check", return_value={"ok": True}),
            patch("mcp_autogui.smoke._mcp_describe_check", return_value={"ok": True}),
        ):
            main(["--config", str(path)])
    assert json.loads(capsys.readouterr().out)["effective_config"]["transport"]["port"] == 8651


def test_smoke_marks_missing_treeland_debug_as_blocked():
    with patch("mcp_autogui.smoke.shutil.which", return_value=None):
        assert _treeland_tree_check() == {"ok": False, "reason": "treeland-debug is unavailable"}


def test_smoke_calls_gui_run_describe():
    response = MagicMock()
    response.read.return_value = b'{"jsonrpc":"2.0","result":{"content":[]}}'
    response.headers.get.return_value = "smoke-session"
    response.__enter__.return_value = response
    with patch("mcp_autogui.smoke._DIRECT_OPENER.open", return_value=response) as request:
        result = _mcp_describe_check("https://mcp.example/mcp")

    assert result == {"ok": True, "endpoint": "https://mcp.example/mcp"}
    initialize = json.loads(request.call_args_list[0].args[0].data)
    describe = json.loads(request.call_args_list[1].args[0].data)
    assert initialize["method"] == "initialize"
    assert describe["method"] == "tools/call"
    assert describe["params"] == {"name": "gui_run", "arguments": {"operation": "describe"}}
    assert request.call_args_list[1].args[0].headers["Mcp-session-id"] == "smoke-session"


def test_smoke_decodes_fastmcp_sse_response():
    assert _decode_mcp_response('event: message\ndata: {"result":{"ok":true}}\n') == {
        "result": {"ok": True}
    }
