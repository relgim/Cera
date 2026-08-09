from __future__ import annotations

import unittest

from cera.errors import ContractValidationError
from cera.sillytavern.models import SillyTavernChatRequest


def _request(stream: object) -> dict[str, object]:
    return {
        "model": "cera-alpha",
        "messages": [{"role": "user", "content": "Continue."}],
        "stream": stream,
    }


class SillyTavernRequestStrictnessTests(unittest.TestCase):
    def test_false_boolean_is_accepted(self) -> None:
        request = SillyTavernChatRequest.from_mapping(_request(False))
        self.assertIs(request.stream, False)

    def test_string_false_is_not_coerced_to_true(self) -> None:
        with self.assertRaisesRegex(ContractValidationError, "stream must be boolean"):
            SillyTavernChatRequest.from_mapping(_request("false"))

    def test_numeric_zero_is_not_coerced_to_false(self) -> None:
        with self.assertRaisesRegex(ContractValidationError, "stream must be boolean"):
            SillyTavernChatRequest.from_mapping(_request(0))


if __name__ == "__main__":
    unittest.main()
