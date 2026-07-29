import logging
import unittest

from libraries.sdgbtech_bridge import (
    _QRLogFilter,
    QR_PATTERN,
    redact_qr_text,
)


class QRRedactionTests(unittest.TestCase):
    def test_raw_qr_is_redacted(self):
        raw = "SGWCMAID" + "A" * 32
        self.assertIsNotNone(QR_PATTERN.search(raw))
        redacted = redact_qr_text(f"received {raw}")
        self.assertEqual("received SGWCMAID[REDACTED]", redacted)
        self.assertNotIn(raw, redacted)

    def test_filter_redacts_message_and_arguments(self):
        raw = "SGWCMAID" + "B" * 32
        record = logging.LogRecord(
            "test", logging.INFO, __file__, 1,
            "value=%s", (raw,), None,
        )
        self.assertTrue(_QRLogFilter().filter(record))
        self.assertNotIn(raw, record.getMessage())


if __name__ == "__main__":
    unittest.main()
