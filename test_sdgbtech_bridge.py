import importlib.util
import unittest
from pathlib import Path

from astrbot_plugin_maibot.libraries.sdgbtech_bridge import (
    BridgeError,
    call_backend,
    register_backend,
    unregister_backend,
)

_TECH_HELPERS_PATH = Path(__file__).parent / "command" / "mai_tech.py"
_TECH_HELPERS_SPEC = importlib.util.spec_from_file_location(
    "maibot_tech_helpers_for_test",
    _TECH_HELPERS_PATH,
)
assert _TECH_HELPERS_SPEC and _TECH_HELPERS_SPEC.loader
_TECH_HELPERS = importlib.util.module_from_spec(_TECH_HELPERS_SPEC)
_TECH_HELPERS_SPEC.loader.exec_module(_TECH_HELPERS)
parse_parameters = _TECH_HELPERS.parse_parameters


class FakeBackend:
    protocol_version = 1

    async def status(self):
        return {"enabled": False, "ready": True}

    async def preview(self, **_kwargs):
        return {"payload": "must not cross bridge"}


class BridgeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncTearDown(self):
        unregister_backend(getattr(self, "backend", None))

    async def test_registers_versioned_backend_and_calls_it(self):
        self.backend = FakeBackend()
        register_backend(self.backend)
        self.assertEqual(
            await call_backend("status"),
            {"enabled": False, "ready": True},
        )

    async def test_rejects_sensitive_backend_response(self):
        self.backend = FakeBackend()
        register_backend(self.backend)
        with self.assertRaises(BridgeError) as context:
            await call_backend("preview")
        self.assertEqual(context.exception.code, "UNSAFE_RESPONSE")

    async def test_rejects_unknown_backend_method(self):
        with self.assertRaises(BridgeError):
            await call_backend("run_script", path="anything.py")

    async def test_parameter_parser_rejects_shell_and_json(self):
        self.assertEqual(
            parse_parameters("value=10,mode=true"),
            {"value": "10", "mode": "true"},
        )
        for value in ["value=1;whoami", 'payload={"x":1}', "../file=x"]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse_parameters(value)


if __name__ == "__main__":
    unittest.main()
