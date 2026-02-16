"""Unit tests for core bms.py logic loaded via AST.

These tests intentionally avoid importing bms.py directly, because the script
contains startup/runtime loop side effects at module level.
"""

import ast
import json
import pathlib
import socket
import time as pytime
import types
import unittest


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
BMS_PATH = REPO_ROOT / "bms.py"


def load_bms_namespace(function_names):
    """Compile only selected function defs from bms.py into an isolated namespace."""
    source = BMS_PATH.read_text()
    tree = ast.parse(source)
    selected = [
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name in function_names
    ]
    module = ast.Module(body=selected, type_ignores=[])
    code = compile(module, str(BMS_PATH), "exec")

    ns = {
        "socket": socket,
        "time": pytime,
        "debug_output": 0,
        "connection_type": "IP",
        "tcp_rx_buffer": b"",
    }
    exec(code, ns)
    return ns


def build_valid_frame(ns, info=b"ABCD", rtn=b"00"):
    """Build a protocol frame using the same checksum helpers as production code."""
    lenid = bytes(format(len(info), "03X"), "ascii")
    lchksum = ns["lchksum_calc"](lenid)
    frame_wo_chk = b"~250146" + rtn + bytes(lchksum, "ascii") + lenid + info
    chksum = ns["chksum_calc"](frame_wo_chk)
    return frame_wo_chk + bytes(chksum, "ascii") + b"\r"


class FakeSocket:
    """Socket stub that returns pre-seeded chunks from recv()."""

    def __init__(self, responses=None, timeout=False):
        self.responses = list(responses or [])
        self.timeout = timeout

    def recv(self, _size):
        if self.responses:
            return self.responses.pop(0)
        if self.timeout:
            raise socket.timeout()
        return b""


class FastTime:
    """Monotonic time stub to force deterministic timeout behavior in tests."""

    def __init__(self):
        self.t = 0

    def monotonic(self):
        self.t += 1
        return self.t


class SleepRecorder:
    """Sleep stub that records delays instead of actually sleeping."""

    def __init__(self):
        self.calls = []

    def sleep(self, seconds):
        self.calls.append(seconds)


class DummyClient:
    """MQTT client stub that records publish() calls."""

    def __init__(self):
        self.published = []

    def publish(self, topic, payload, **kwargs):
        self.published.append((topic, payload, kwargs))


class DummyConnectClient:
    """MQTT connect stub with configurable failure mode."""

    def __init__(self, mode="ok"):
        self.mode = mode

    def connect(self, *_args, **_kwargs):
        if self.mode == "ok":
            return 0
        if self.mode == "timeout":
            raise socket.timeout()
        if self.mode == "oserror":
            raise OSError("connect failed")
        raise RuntimeError("generic connect failure")


class TestParseData(unittest.TestCase):
    """Frame validation and parser error-path tests."""

    def setUp(self):
        self.ns = load_bms_namespace(
            {"cid2_rtn", "lchksum_calc", "chksum_calc", "bms_parse_data"}
        )

    def test_valid_frame_returns_info(self):
        """Valid protocol frame should parse and return INFO bytes."""
        frame = build_valid_frame(self.ns, info=b"ABCD")
        ok, info = self.ns["bms_parse_data"](frame)
        self.assertTrue(ok)
        self.assertEqual(info, b"ABCD")

    def test_rtn_error_uses_descriptive_message(self):
        """RTN errors should surface human-readable RTN details."""
        frame = build_valid_frame(self.ns, info=b"ABCD", rtn=b"09")
        ok, message = self.ns["bms_parse_data"](frame)
        self.assertFalse(ok)
        self.assertIn("RTN Error 09", message)

    def test_checksum_error_is_detected(self):
        """Mutating payload checksum should be detected as checksum error."""
        frame = bytearray(build_valid_frame(self.ns, info=b"ABCD"))
        frame[-2] = ord("0") if frame[-2] != ord("0") else ord("1")
        ok, message = self.ns["bms_parse_data"](bytes(frame))
        self.assertFalse(ok)
        self.assertEqual(message, "Checksum error")


class TestGetDataBuffering(unittest.TestCase):
    """TCP receive buffering and frame extraction behavior."""

    def setUp(self):
        self.ns = load_bms_namespace({"bms_get_data", "lchksum_calc", "chksum_calc"})
        self.frame1 = build_valid_frame(self.ns, info=b"ABCD")
        self.frame2 = build_valid_frame(self.ns, info=b"WXYZ")

    def test_partial_frame_is_reassembled(self):
        """Frame split across recv() calls should reassemble into one frame."""
        fake = FakeSocket(
            responses=[
                self.frame1[:5],
                self.frame1[5:12],
                self.frame1[12:],
            ]
        )
        got = self.ns["bms_get_data"](fake)
        self.assertEqual(got, self.frame1)

    def test_multiple_frames_keep_remainder_in_buffer(self):
        """If two frames arrive together, second should remain buffered."""
        fake = FakeSocket(responses=[self.frame1 + self.frame2])
        first = self.ns["bms_get_data"](fake)
        second = self.ns["bms_get_data"](fake)
        self.assertEqual(first, self.frame1)
        self.assertEqual(second, self.frame2)

    def test_noise_preamble_is_ignored(self):
        """Bytes before SOI should be discarded when extracting frame."""
        fake = FakeSocket(responses=[b"NOISE" + self.frame1])
        got = self.ns["bms_get_data"](fake)
        self.assertEqual(got, self.frame1)

    def test_timeout_returns_false(self):
        """Repeated socket timeout without full frame should return False."""
        self.ns["time"] = FastTime()
        fake = FakeSocket(timeout=True)
        got = self.ns["bms_get_data"](fake)
        self.assertFalse(got)


class TestPackNumberFallback(unittest.TestCase):
    """Pack count detection behavior with ADR probing and fallback."""

    def setUp(self):
        self.ns = load_bms_namespace({"bms_getPackNumber"})
        self.ns["constants"] = types.SimpleNamespace(
            cid2PackNumber=b"90",
            cid2PackAnalogData=b"42",
        )
        self.ns["debug_output"] = 0

    def test_returns_early_on_multi_pack_report(self):
        """Pack-number command should return immediately when >1 is reported."""

        def fake_request(_bms, adr=None, cid2=None, info=None):
            if cid2 == self.ns["constants"].cid2PackNumber and adr == b"FF":
                return True, b"03"
            return False, b""

        self.ns["bms_request"] = fake_request
        ok, count = self.ns["bms_getPackNumber"](object())
        self.assertTrue(ok)
        self.assertEqual(count, 3)

    def test_falls_back_to_analog_payload(self):
        """If ADR probes are inconclusive, analog payload should provide count."""

        def fake_request(_bms, adr=None, cid2=None, info=None):
            if cid2 == self.ns["constants"].cid2PackNumber:
                return True, b"01"
            if cid2 == self.ns["constants"].cid2PackAnalogData:
                return True, b"0003"
            return False, b""

        self.ns["bms_request"] = fake_request
        ok, count = self.ns["bms_getPackNumber"](object())
        self.assertTrue(ok)
        self.assertEqual(count, 3)


class TestAnalogAndDiscovery(unittest.TestCase):
    """Analog parser realignment, signed current, and SOC/SOH guard behavior."""

    def setUp(self):
        self.ns = load_bms_namespace({"bms_getAnalogData"})
        self.ns["constants"] = types.SimpleNamespace(cid2PackAnalogData=b"42")
        self.ns["config"] = {"mqtt_base_topic": "bmspace"}
        self.ns["client"] = DummyClient()
        self.ns["print_initial"] = False
        self.ns["cells"] = 13
        self.ns["temps"] = 6
        self.ns["packs"] = 1
        self.ns["bms_force_pack_offset"] = 0
        self.ns["debug_output"] = 0

    def _build_two_pack_analog_info(self, insert_extra_word=False):
        """Build minimal valid two-pack analog payload used by alignment tests."""
        chunks = []
        chunks.append("00")  # prefix byte (parser starts at byte_index=2)
        chunks.append("02")  # packs
        for _ in range(2):
            chunks.append("01")  # cells
            chunks.append("0CE4")  # single cell voltage
            chunks.append("01")  # temps
            chunks.append("0BB8")  # single temp raw
            chunks.append("0001")  # i_pack
            chunks.append("C350")  # v_pack
            chunks.append("2710")  # i_remain_cap
            chunks.append("00")  # manual skip field
            chunks.append("2710")  # i_full_cap
            chunks.append("0010")  # cycles
            chunks.append("2710")  # i_design_cap
            chunks.append("00")  # trailing skip field
            if insert_extra_word:
                chunks.append("AA")  # extra word to force realignment scan
        return "".join(chunks).encode("ascii")

    def _build_one_pack_with_current(self, current_hex):
        """Build one-pack payload with configurable raw current field."""
        chunks = [
            "00",
            "01",  # packs
            "01",  # cells
            "0CE4",  # cell voltage
            "01",  # temps
            "0BB8",  # temp
            current_hex,  # i_pack raw
            "C350",  # v_pack
            "2710",  # remain
            "00",  # skip
            "2710",  # full
            "0010",  # cycles
            "2710",  # design
            "00",  # trailing
        ]
        return "".join(chunks).encode("ascii")

    def test_multi_pack_realign_scan_handles_extra_words(self):
        """Realignment scan should tolerate extra bytes between pack blocks."""
        info = self._build_two_pack_analog_info(insert_extra_word=True)
        self.ns["bms_request"] = lambda *_args, **_kwargs: (True, info)

        ok, result = self.ns["bms_getAnalogData"](object(), batNumber=255)
        self.assertTrue(ok)
        self.assertTrue(result)

    def test_force_pack_offset_applied(self):
        """Configured force offset should still allow multi-pack parse to succeed."""
        info = self._build_two_pack_analog_info(insert_extra_word=False)
        self.ns["bms_request"] = lambda *_args, **_kwargs: (True, info)
        self.ns["bms_force_pack_offset"] = 2

        ok, result = self.ns["bms_getAnalogData"](object(), batNumber=255)
        self.assertTrue(ok)
        self.assertTrue(result)

    def test_soc_soh_zero_division_guards(self):
        """Zero capacities should force SOC/SOH to 0 instead of raising error."""
        chunks = [
            "00", "01", "01", "0CE4", "01", "0BB8", "0001", "C350",
            "2710", "00", "0000", "0010", "0000", "00",
        ]
        info = "".join(chunks).encode("ascii")
        self.ns["bms_request"] = lambda *_args, **_kwargs: (True, info)
        self.ns["debug_output"] = 1

        ok, result = self.ns["bms_getAnalogData"](object(), batNumber=255)
        self.assertTrue(ok)
        self.assertTrue(result)

        topics = dict((t, p) for (t, p, _k) in self.ns["client"].published)
        self.assertEqual(topics["bmspace/pack_1/soc"], "0")
        self.assertEqual(topics["bmspace/pack_1/soh"], "0")

    def test_signed_current_ffff_decodes_to_minus_point_zero_one(self):
        """0xFFFF should decode to -0.01 A after two's-complement conversion."""
        info = self._build_one_pack_with_current("FFFF")
        self.ns["bms_request"] = lambda *_args, **_kwargs: (True, info)

        ok, _result = self.ns["bms_getAnalogData"](object(), batNumber=255)
        self.assertTrue(ok)

        topics = dict((t, p) for (t, p, _k) in self.ns["client"].published)
        self.assertEqual(topics["bmspace/pack_1/i_pack"], "-0.01")

    def test_signed_current_8000_decodes_to_minus_327_point_68(self):
        """0x8000 should decode to -327.68 A boundary value."""
        info = self._build_one_pack_with_current("8000")
        self.ns["bms_request"] = lambda *_args, **_kwargs: (True, info)

        ok, _result = self.ns["bms_getAnalogData"](object(), batNumber=255)
        self.assertTrue(ok)

        topics = dict((t, p) for (t, p, _k) in self.ns["client"].published)
        self.assertEqual(topics["bmspace/pack_1/i_pack"], "-327.68")

    def test_zero_padding_applies_to_runtime_topic_names(self):
        """Configured pack/cell zero-padding should be reflected in MQTT runtime topics."""
        info = self._build_one_pack_with_current("0001")
        self.ns["bms_request"] = lambda *_args, **_kwargs: (True, info)
        self.ns["fmt_pack"] = lambda n: str(n).zfill(2)
        self.ns["fmt_cell"] = lambda n: str(n).zfill(2)

        ok, _result = self.ns["bms_getAnalogData"](object(), batNumber=255)
        self.assertTrue(ok)

        topics = [t for (t, _p, _k) in self.ns["client"].published]
        self.assertIn("bmspace/pack_01/v_cells/cell_01", topics)
        self.assertIn("bmspace/pack_01/i_pack", topics)
        self.assertNotIn("bmspace/pack_1/v_cells/cell_1", topics)


class TestPackCapacityAndWarnings(unittest.TestCase):
    """Pack-capacity guard behavior and warning pack-loop correctness."""

    def test_pack_capacity_zero_division_guards(self):
        """Pack-level SOC/SOH should be forced to 0 when divisors are zero."""
        ns = load_bms_namespace({"bms_getPackCapacity"})
        ns["config"] = {"mqtt_base_topic": "bmspace"}
        ns["client"] = DummyClient()
        ns["print_initial"] = False
        ns["debug_output"] = 1
        ns["constants"] = types.SimpleNamespace(cid2PackCapacity=b"A6")
        ns["bms_request"] = lambda *_a, **_k: (True, b"271000000000")

        ok, result = ns["bms_getPackCapacity"](object())
        self.assertTrue(ok)
        self.assertTrue(result)

        topics = dict((t, p) for (t, p, _k) in ns["client"].published)
        self.assertEqual(topics["bmspace/pack_soc"], "0")
        self.assertEqual(topics["bmspace/pack_soh"], "0")

    def test_warning_parser_uses_packsw_not_global_packs(self):
        """Warnings should only publish for packs present in warning payload."""
        ns = load_bms_namespace({"bms_getWarnInfo"})
        ns["config"] = {"mqtt_base_topic": "bmspace"}
        ns["client"] = DummyClient()
        ns["print_initial"] = False
        ns["packs"] = 3  # Should be ignored by parser loop.
        ns["cells"] = 1
        ns["constants"] = types.SimpleNamespace(
            cid2WarnInfo=b"44",
            warningStates={b"00": "Normal"},
            protectState1={i: f"p1_{i}" for i in range(1, 9)},
            protectState2={i: f"p2_{i}" for i in range(1, 9)},
            controlState={i: f"c_{i}" for i in range(1, 9)},
            faultState={i: f"f_{i}" for i in range(1, 9)},
            warnState1={i: f"w1_{i}" for i in range(1, 9)},
            warnState2={i: f"w2_{i}" for i in range(1, 9)},
        )

        info = ("00" + "01" + "00" + "00" + ("00" * 12)).encode("ascii")
        ns["bms_request"] = lambda *_a, **_k: (True, info)

        ok, result = ns["bms_getWarnInfo"](object())
        self.assertTrue(ok)
        self.assertTrue(result)

        topics = [t for (t, _p, _k) in ns["client"].published]
        self.assertTrue(any("/pack_1/" in t for t in topics))
        self.assertFalse(any("/pack_2/" in t for t in topics))


class TestDiscoveryAggregateEntities(unittest.TestCase):
    """HA discovery aggregate entities should be published exactly once."""

    def test_aggregate_discovery_published_once(self):
        """Aggregate pack sensors should not be duplicated per pack loop."""
        ns = load_bms_namespace({"ha_discovery"})
        ns["json"] = json
        ns["disc_payload"] = {}
        ns["ha_discovery_enabled"] = True
        ns["packs"] = 2
        ns["cells"] = 1
        ns["temps"] = 1
        ns["bms_sn"] = "SN123"
        ns["bms_version"] = "v1"
        ns["config"] = {
            "mqtt_base_topic": "bmspace",
            "mqtt_ha_discovery_topic": "homeassistant",
        }
        ns["client"] = DummyClient()

        ns["ha_discovery"]()

        aggregate_states = []
        for (_topic, payload, _k) in ns["client"].published:
            obj = json.loads(payload)
            st = obj.get("state_topic")
            if st in {
                "bmspace/pack_remain_cap",
                "bmspace/pack_full_cap",
                "bmspace/pack_design_cap",
                "bmspace/pack_soc",
                "bmspace/pack_soh",
            }:
                aggregate_states.append(st)

        self.assertEqual(
            sorted(aggregate_states),
            sorted(
                {
                    "bmspace/pack_remain_cap",
                    "bmspace/pack_full_cap",
                    "bmspace/pack_design_cap",
                    "bmspace/pack_soc",
                    "bmspace/pack_soh",
                }
            ),
        )
        self.assertEqual(len(aggregate_states), 5)

    def test_discovery_respects_zero_padding_for_pack_and_cell_ids(self):
        """Discovery payload should use padded pack/cell numbering when configured."""
        ns = load_bms_namespace({"ha_discovery"})
        ns["json"] = json
        ns["disc_payload"] = {}
        ns["ha_discovery_enabled"] = True
        ns["packs"] = 1
        ns["cells"] = 1
        ns["temps"] = 1
        ns["bms_sn"] = "SN123"
        ns["bms_version"] = "v1"
        ns["config"] = {
            "mqtt_base_topic": "bmspace",
            "mqtt_ha_discovery_topic": "homeassistant",
        }
        ns["client"] = DummyClient()
        ns["fmt_pack"] = lambda n: str(n).zfill(2)
        ns["fmt_cell"] = lambda n: str(n).zfill(2)

        ns["ha_discovery"]()

        objects = [json.loads(payload) for (_topic, payload, _k) in ns["client"].published]
        cell_obj = next((o for o in objects if o.get("name") == "Pack 01 Cell 01 Voltage"), None)
        self.assertIsNotNone(cell_obj)
        self.assertEqual(cell_obj["state_topic"], "bmspace/pack_01/v_cells/cell_01")


class TestRetryHelpers(unittest.TestCase):
    """Connection retry helper behavior for BMS and MQTT wrappers."""

    def test_bms_connect_with_retries_succeeds_on_later_attempt(self):
        """Retry helper should stop after first successful attempt."""
        ns = load_bms_namespace({"bms_connect_with_retries"})
        ns["bms_connect_retries"] = 5
        ns["bms_connect_retry_delay"] = 2

        sleep = SleepRecorder()
        ns["time"] = types.SimpleNamespace(sleep=sleep.sleep)

        attempts = {"n": 0}

        def fake_connect(_address, _port):
            attempts["n"] += 1
            if attempts["n"] < 3:
                return False, False
            return "COMMS", True

        ns["bms_connect"] = fake_connect

        comms, ok = ns["bms_connect_with_retries"]("1.2.3.4", 5000)
        self.assertTrue(ok)
        self.assertEqual(comms, "COMMS")
        self.assertEqual(attempts["n"], 3)
        self.assertEqual(sleep.calls, [2, 2])

    def test_bms_connect_with_retries_returns_false_after_max(self):
        """Retry helper should return failure after exhausting attempts."""
        ns = load_bms_namespace({"bms_connect_with_retries"})
        ns["bms_connect_retries"] = 3
        ns["bms_connect_retry_delay"] = 1

        sleep = SleepRecorder()
        ns["time"] = types.SimpleNamespace(sleep=sleep.sleep)
        ns["bms_connect"] = lambda _address, _port: (False, False)

        comms, ok = ns["bms_connect_with_retries"]("1.2.3.4", 5000)
        self.assertFalse(ok)
        self.assertFalse(comms)
        self.assertEqual(sleep.calls, [1, 1])

    def test_mqtt_connect_timeout_sets_disconnected(self):
        """MQTT connect wrapper should set mqtt_connected False on timeout."""
        ns = load_bms_namespace({"mqtt_connect"})
        ns["config"] = {"mqtt_host": "localhost", "mqtt_port": 1883}
        ns["client"] = DummyConnectClient(mode="timeout")
        ns["mqtt_connected"] = True

        ok = ns["mqtt_connect"]()
        self.assertFalse(ok)
        self.assertFalse(ns["mqtt_connected"])

    def test_mqtt_connect_oserror_sets_disconnected(self):
        """MQTT connect wrapper should set mqtt_connected False on OSError."""
        ns = load_bms_namespace({"mqtt_connect"})
        ns["config"] = {"mqtt_host": "localhost", "mqtt_port": 1883}
        ns["client"] = DummyConnectClient(mode="oserror")
        ns["mqtt_connected"] = True

        ok = ns["mqtt_connect"]()
        self.assertFalse(ok)
        self.assertFalse(ns["mqtt_connected"])


if __name__ == "__main__":
    unittest.main()
