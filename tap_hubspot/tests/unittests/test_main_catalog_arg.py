import json
import tempfile
import unittest
from unittest import mock

import tap_hubspot

CATALOG = {"streams": [{"tap_stream_id": "tickets", "stream": "tickets",
                        "schema": {"type": "object", "properties": {}},
                        "metadata": [{"breadcrumb": [], "metadata": {"selected": True}}]}]}


def _write(data):
    f = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
    json.dump(data, f)
    f.close()
    return f.name


class TestMainCatalogArg(unittest.TestCase):
    def _run(self, flag):
        argv = ["tap-hubspot", "--config", _write({"api_key": "x", "start_date": "2020-01-01T00:00:00Z"}),
                flag, _write(CATALOG)]
        with mock.patch("sys.argv", argv), mock.patch.object(tap_hubspot, "do_sync") as do_sync:
            tap_hubspot.main_impl()
        return do_sync

    def test_catalog_flag_runs_sync(self):
        do_sync = self._run("--catalog")
        do_sync.assert_called_once()
        self.assertEqual(do_sync.call_args[0][1]["streams"][0]["tap_stream_id"], "tickets")

    def test_properties_flag_runs_sync(self):
        do_sync = self._run("--properties")
        do_sync.assert_called_once()
        self.assertEqual(do_sync.call_args[0][1], CATALOG)

    def test_no_selection_skips_sync(self):
        argv = ["tap-hubspot", "--config", _write({"api_key": "x", "start_date": "2020-01-01T00:00:00Z"})]
        with mock.patch("sys.argv", argv), mock.patch.object(tap_hubspot, "do_sync") as do_sync:
            tap_hubspot.main_impl()
        do_sync.assert_not_called()
