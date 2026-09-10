import unittest

from agentic_plc.protocols.modbus import (
    ModbusFrameError,
    build_modbus_tcp_exception_response,
    build_modbus_tcp_read_registers_response,
    parse_modbus_tcp_frame,
    parse_modbus_tcp_request,
)


class ModbusProtocolTests(unittest.TestCase):
    def test_build_and_parse_read_registers_response(self) -> None:
        payload_hex = build_modbus_tcp_read_registers_response(
            transaction_id=17,
            unit_id=1,
            function_code=3,
            values=[500, 120],
        )

        frame = parse_modbus_tcp_frame(payload_hex)

        self.assertEqual(frame.transaction_id, 17)
        self.assertEqual(frame.unit_id, 1)
        self.assertEqual(frame.function_code, 3)
        self.assertEqual(frame.data, bytes.fromhex("04 01 f4 00 78"))

    def test_build_and_parse_exception_response(self) -> None:
        payload_hex = build_modbus_tcp_exception_response(
            transaction_id=17,
            unit_id=1,
            function_code=3,
            exception_code=2,
        )

        frame = parse_modbus_tcp_frame(payload_hex)

        self.assertTrue(frame.is_exception)
        self.assertEqual(frame.function_code, 0x83)
        self.assertEqual(frame.data, bytes([2]))

    def test_rejects_bad_mbap_length(self) -> None:
        with self.assertRaisesRegex(ModbusFrameError, "length"):
            parse_modbus_tcp_frame("00 11 00 00 00 08 01 03 02 00 01")

    def test_parse_read_registers_request(self) -> None:
        request = bytes.fromhex("00 11 00 00 00 06 01 03 00 00 00 02")

        parsed = parse_modbus_tcp_request(request)

        self.assertEqual(parsed.transaction_id, 17)
        self.assertEqual(parsed.unit_id, 1)
        self.assertEqual(parsed.function_code, 3)
        self.assertEqual(parsed.address, 0)
        self.assertEqual(parsed.count, 2)

    def test_parse_write_multiple_registers_request(self) -> None:
        request = bytes.fromhex("00 12 00 00 00 0b 01 10 00 00 00 02 04 02 bc 02 d0")

        parsed = parse_modbus_tcp_request(request)

        self.assertEqual(parsed.transaction_id, 18)
        self.assertEqual(parsed.function_code, 16)
        self.assertEqual(parsed.address, 0)
        self.assertEqual(parsed.count, 2)
        self.assertEqual(parsed.values, (700, 720))


if __name__ == "__main__":
    unittest.main()
