from __future__ import annotations

import unittest

from agentic_plc.contracts.actions import ProtocolReply
from agentic_plc.contracts.events import ICSEvent, Intent
from agentic_plc.policy.protocol_reply_validator import ProtocolReplyValidator
from agentic_plc.protocols.modbus import (
    build_modbus_tcp_exception_response,
    build_modbus_tcp_read_bits_response,
    build_modbus_tcp_read_registers_response,
    build_modbus_tcp_write_multiple_response,
    build_modbus_tcp_write_single_response,
)


class ProtocolReplyValidatorTests(unittest.TestCase):
    def test_accepts_read_bits_response_matching_request_shape(self) -> None:
        reply = ProtocolReply(
            protocol="modbus_tcp",
            transaction_id="17",
            unit_id=1,
            payload_hex=build_modbus_tcp_read_bits_response(
                transaction_id=17,
                unit_id=1,
                function_code=1,
                values=[1, 0, 1, 1, 0, 0, 0, 1, 1],
            ),
            reason="generated coil read",
        )

        ProtocolReplyValidator().validate(reply, [_event(1, count=9)])

    def test_accepts_write_single_echo_matching_request(self) -> None:
        reply = ProtocolReply(
            protocol="modbus_tcp",
            transaction_id="18",
            unit_id=1,
            payload_hex=build_modbus_tcp_write_single_response(
                transaction_id=18,
                unit_id=1,
                function_code=6,
                address=5,
                value=700,
            ),
            reason="generated write ack",
        )

        ProtocolReplyValidator().validate(
            reply,
            [_event(6, transaction_id="18", address=5, requested_value=700)],
        )

    def test_accepts_write_multiple_echo_matching_request(self) -> None:
        reply = ProtocolReply(
            protocol="modbus_tcp",
            transaction_id="19",
            unit_id=1,
            payload_hex=build_modbus_tcp_write_multiple_response(
                transaction_id=19,
                unit_id=1,
                function_code=16,
                address=5,
                count=2,
            ),
            reason="generated multiple write ack",
        )

        ProtocolReplyValidator().validate(
            reply,
            [_event(16, transaction_id="19", address=5, count=2)],
        )

    def test_accepts_exception_when_base_function_matches_request(self) -> None:
        reply = ProtocolReply(
            protocol="modbus_tcp",
            transaction_id="17",
            unit_id=1,
            payload_hex=build_modbus_tcp_exception_response(
                transaction_id=17,
                unit_id=1,
                function_code=3,
                exception_code=2,
            ),
            reason="generated exception",
        )

        ProtocolReplyValidator().validate(reply, [_event(3, count=2)])

    def test_rejects_response_function_mismatch(self) -> None:
        reply = ProtocolReply(
            protocol="modbus_tcp",
            transaction_id="17",
            unit_id=1,
            payload_hex=build_modbus_tcp_read_registers_response(
                transaction_id=17,
                unit_id=1,
                function_code=3,
                values=[500, 120],
            ),
            reason="wrong function",
        )

        with self.assertRaisesRegex(ValueError, "function_code"):
            ProtocolReplyValidator().validate(reply, [_event(4, count=2)])

    def test_rejects_read_count_mismatch(self) -> None:
        reply = ProtocolReply(
            protocol="modbus_tcp",
            transaction_id="17",
            unit_id=1,
            payload_hex=build_modbus_tcp_read_registers_response(
                transaction_id=17,
                unit_id=1,
                function_code=3,
                values=[500],
            ),
            reason="wrong count",
        )

        with self.assertRaisesRegex(ValueError, "byte count"):
            ProtocolReplyValidator().validate(reply, [_event(3, count=2)])

    def test_rejects_write_address_mismatch(self) -> None:
        reply = ProtocolReply(
            protocol="modbus_tcp",
            transaction_id="18",
            unit_id=1,
            payload_hex=build_modbus_tcp_write_single_response(
                transaction_id=18,
                unit_id=1,
                function_code=6,
                address=6,
                value=700,
            ),
            reason="wrong address",
        )

        with self.assertRaisesRegex(ValueError, "address"):
            ProtocolReplyValidator().validate(
                reply,
                [_event(6, transaction_id="18", address=5, requested_value=700)],
            )


def _event(
    function_code: int,
    *,
    transaction_id: str = "17",
    address: int = 0,
    count: int | None = 1,
    requested_value: int | None = None,
) -> ICSEvent:
    intent = (
        Intent.READ_PROCESS
        if function_code in {1, 2, 3, 4}
        else Intent.WRITE_SETPOINT
    )
    operation = {
        1: "read_coils",
        2: "read_discrete_inputs",
        3: "read_holding_registers",
        4: "read_input_registers",
        5: "write_single_coil",
        6: "write_single_register",
        15: "write_multiple_coils",
        16: "write_multiple_registers",
    }[function_code]
    return ICSEvent(
        protocol="modbus",
        session_id="validator-test",
        source_ip="192.0.2.10",
        actor_id="actor-1",
        intent=intent,
        operation=operation,
        transaction_id=transaction_id,
        unit_id=1,
        address=address,
        count=count,
        requested_value=requested_value,
        result="observed",
        metadata={"function_code": function_code},
    )


if __name__ == "__main__":
    unittest.main()
