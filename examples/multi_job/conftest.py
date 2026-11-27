"""Optional explicit links to existing Dash runs. Never create or discover runs."""
import json
from pathlib import Path
import re
from urllib.parse import unquote, urlsplit

import pytest


def pytest_addoption(parser):
    parser.addoption("--dash-links", help="Explicit JSON map from pytest node ID to an existing canonical Dash run URL")


def pytest_configure(config):
    config._example_dash_links = {}
    path = config.getoption("dash_links")
    if not path:
        return
    try:
        links = json.loads(Path(path).read_text())
        if not isinstance(links, dict):
            raise ValueError()
        for node, uri in links.items():
            if not isinstance(node, str) or not node or not isinstance(uri, str):
                raise ValueError()
            if not uri.startswith("https://dreamlake.ai/") or re.search(r"[\x00-\x1f\x7f]|%(?![0-9a-fA-F]{2})", uri):
                raise ValueError()
            url = urlsplit(uri)
            match = re.fullmatch(r"/([^/]+)/dash/([^/]+)/([^/]+)", url.path)
            if url.netloc != "dreamlake.ai" or url.query or url.fragment or not match:
                raise ValueError()
            if any(part in (".", "..") or re.search(r"[\x00-\x1f\x7f]", part)
                   for part in (unquote(p, errors="strict") for p in match.groups())):
                raise ValueError()
        config._example_dash_links = links
    except (ValueError, OSError):
        raise pytest.UsageError("--dash-links must map exact pytest node IDs to canonical https://dreamlake.ai/NAMESPACE/dash/PROJECT/RUN URLs without query or fragment") from None


@pytest.fixture(autouse=True)
def record_explicit_dash_run(request):
    uri = request.config._example_dash_links.get(request.node.nodeid)
    if uri:
        # The fixture runs within this trial's attempt, including async tests.
        # No HTTP request, upload, workload or ownership assertion occurs here.
        request.getfixturevalue("eval_trial").artifact(uri, name="Dash run")
