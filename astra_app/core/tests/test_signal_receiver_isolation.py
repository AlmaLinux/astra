import importlib
from typing import Any

from django.dispatch import Signal
from django.test import SimpleTestCase

from core.models import MembershipRequest, Organization
from core.signals import CANONICAL_SIGNALS


class SafeReceiverIsolationTests(SimpleTestCase):
    def test_safe_receiver_swallows_exception(self) -> None:
        from core.signal_receivers import safe_receiver

        signal = Signal()

        @safe_receiver(event_key="test_event")
        def failing_receiver(*args: Any, **kwargs: Any) -> None:
            _ = (args, kwargs)
            raise ValueError("boom")

        signal.connect(failing_receiver, dispatch_uid="test.safe_receiver.error")
        try:
            with self.assertLogs("core.signal_receivers", level="ERROR") as captured:
                signal.send(sender=self.__class__)
        finally:
            signal.disconnect(failing_receiver, dispatch_uid="test.safe_receiver.error")

        self.assertTrue(captured.records)
        record = captured.records[0]
        self.assertEqual(record.getMessage(), "receiver.error")
        self.assertEqual(getattr(record, "event_key", None), "test_event")
        self.assertEqual(getattr(record, "exc_type", None), "ValueError")
        self.assertEqual(getattr(record, "exc_message", None), "boom")
        self.assertIsNotNone(record.exc_info)

    def test_safe_receiver_logs_success(self) -> None:
        from core.signal_receivers import safe_receiver

        signal = Signal()

        @safe_receiver(event_key="test_event")
        def ok_receiver(*args: Any, **kwargs: Any) -> None:
            _ = (args, kwargs)

        signal.connect(ok_receiver, dispatch_uid="test.safe_receiver.ok")
        try:
            with self.assertLogs("core.signal_receivers", level="DEBUG") as captured:
                signal.send(sender=self.__class__)
        finally:
            signal.disconnect(ok_receiver, dispatch_uid="test.safe_receiver.ok")

        self.assertTrue(captured.records)
        record = captured.records[0]
        self.assertEqual(record.getMessage(), "receiver.ok")
        self.assertEqual(getattr(record, "event_key", None), "test_event")
        receiver_name = str(getattr(record, "receiver", ""))
        self.assertTrue(receiver_name.endswith("ok_receiver"))
        self.assertGreaterEqual(getattr(record, "duration_ms", -1), 0)

    def test_safe_receiver_does_not_swallow_base_exceptions(self) -> None:
        from core.signal_receivers import safe_receiver

        signal = Signal()

        @safe_receiver(event_key="test_event")
        def interrupting_receiver(*args: Any, **kwargs: Any) -> None:
            _ = (args, kwargs)
            raise KeyboardInterrupt()

        signal.connect(interrupting_receiver, dispatch_uid="test.safe_receiver.base_exception")
        try:
            with self.assertRaises(KeyboardInterrupt):
                signal.send(sender=self.__class__)
        finally:
            signal.disconnect(interrupting_receiver, dispatch_uid="test.safe_receiver.base_exception")

    def test_safe_receiver_error_log_includes_receiver_and_duration(self) -> None:
        from core.signal_receivers import safe_receiver

        signal = Signal()

        @safe_receiver(event_key="test_event")
        def exploding_receiver(*args: Any, **kwargs: Any) -> None:
            _ = (args, kwargs)
            raise RuntimeError("deliberate failure")

        signal.connect(exploding_receiver, dispatch_uid="test.safe_receiver.error.fields")
        try:
            with self.assertLogs("core.signal_receivers", level="ERROR") as captured:
                signal.send(
                    sender=self.__class__,
                    membership_request=MembershipRequest(pk=123),
                    organization_id=77,
                )
        finally:
            signal.disconnect(exploding_receiver, dispatch_uid="test.safe_receiver.error.fields")

        self.assertTrue(captured.records)
        record = captured.records[0]
        receiver_name = str(getattr(record, "receiver", ""))
        self.assertTrue(
            receiver_name.endswith("exploding_receiver"),
            f"unexpected receiver name: {receiver_name!r}",
        )
        duration_ms = getattr(record, "duration_ms", None)
        self.assertIsNotNone(duration_ms, "duration_ms must be present in error log")
        self.assertIsInstance(duration_ms, float, "duration_ms must be a float")
        self.assertGreaterEqual(float(duration_ms), 0.0)

        self.assertEqual(getattr(record, "membership_request_id", None), 123)
        self.assertEqual(getattr(record, "organization_id", None), 77)


class SignalEmissionLoggingTests(SimpleTestCase):
    def test_emission_log_fires_for_all_canonical_signals(self) -> None:
        importlib.import_module("core.signal_debug")

        for event_key, signal in CANONICAL_SIGNALS.items():
            with self.subTest(event_key=event_key):
                with self.assertLogs("core.signals", level="DEBUG") as captured:
                    signal.send(sender=self.__class__)

                self.assertTrue(captured.records)
                self.assertTrue(
                    any(
                        record.getMessage() == "signal.emit"
                        and getattr(record, "event_key", None) == event_key
                        for record in captured.records
                    )
                )

    def test_emission_log_extracts_actor(self) -> None:
        importlib.import_module("core.signal_debug")

        signal = CANONICAL_SIGNALS["membership_request_submitted"]
        with self.assertLogs("core.signals", level="DEBUG") as captured:
            signal.send(sender=self.__class__, actor="testuser")

        self.assertTrue(captured.records)
        record = captured.records[0]
        self.assertEqual(record.getMessage(), "signal.emit")
        self.assertEqual(getattr(record, "event_key", None), "membership_request_submitted")
        self.assertEqual(getattr(record, "actor", None), "testuser")

    def test_emission_log_extracts_object_ids(self) -> None:
        """object_ids must include both _id scalar kwargs and Model instance PKs."""
        importlib.import_module("core.signal_debug")

        signal = CANONICAL_SIGNALS["organization_membership_request_submitted"]

        with self.assertLogs("core.signals", level="DEBUG") as captured:
            signal.send(
                sender=self.__class__,
                actor="tester",
                organization_id=42,
                request_id="req-123",
                organization=Organization(pk=77),
            )

        records = [record for record in captured.records if record.getMessage() == "signal.emit"]
        self.assertTrue(records, "Expected at least one signal.emit log record")
        record = records[0]
        object_ids = getattr(record, "object_ids", {})
        self.assertIn("organization_id", object_ids)
        self.assertEqual(object_ids["organization_id"], 42)
        self.assertIn("request_id", object_ids)
        self.assertEqual(object_ids["request_id"], "req-123")
        self.assertIn("Organization.pk", object_ids)
        self.assertEqual(object_ids["Organization.pk"], 77)
