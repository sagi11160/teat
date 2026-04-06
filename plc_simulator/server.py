"""
PLC Simulator — Modbus TCP Server simulating a Siemens S7-1200.

Runs an asynchronous Modbus TCP server on port 5020 with holding registers
that represent real PLC memory addresses (40001+). External clients can
read/write registers via standard Modbus function codes.
"""

import asyncio
import logging
import signal
import sys
from typing import Any, Dict, Optional

from pymodbus.datastore import (
    ModbusDeviceContext,
    ModbusSequentialDataBlock,
    ModbusServerContext,
)
from pymodbus.server import StartAsyncTcpServer

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [PLC-SIM] %(levelname)s %(message)s",
)
logger = logging.getLogger("plc_simulator")

# ---------------------------------------------------------------------------
# Register Map (mirrors Siemens S7-1200 style addresses)
# ---------------------------------------------------------------------------
# Address   | Purpose
# ----------|--------------------------------------------------------------
# 40001 (0) | Chiller Command (0=OFF, 1=ON, 2=COOL, 3=HEAT)
# 40002 (1) | Chiller Status  (0=IDLE, 1=RUNNING, 2=FAULT)
# 40003 (2) | Setpoint Temperature (°C × 10, e.g. 225 = 22.5 °C)
# 40004 (3) | Actual Temperature   (°C × 10)
# 40005 (4) | Alarm Register       (bit-field)
# ---------------------------------------------------------------------------

DEFAULT_REGISTER_COUNT = 100
DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 5020


def build_context(initial_values: Optional[Dict[int, int]] = None) -> ModbusServerContext:
    """Build a Modbus server context with pre-initialised holding registers.

    pymodbus 3.12 applies a +1 address offset internally (no zero_mode),
    so we pad the values array by 1 at the front so that Modbus address N
    maps to ``initial_values[N]``.
    """
    # +1 padding at index 0 to compensate for pymodbus internal offset
    values = [0] * (DEFAULT_REGISTER_COUNT + 1)
    if initial_values:
        for addr, val in initial_values.items():
            internal = addr + 1  # shift for pymodbus offset
            if 0 < internal < len(values):
                values[internal] = val

    block = ModbusSequentialDataBlock(0, values)

    slave = ModbusDeviceContext(
        di=ModbusSequentialDataBlock(0, [0] * DEFAULT_REGISTER_COUNT),  # Discrete Inputs
        co=ModbusSequentialDataBlock(0, [0] * DEFAULT_REGISTER_COUNT),  # Coils
        hr=block,                                                       # Holding Registers
        ir=ModbusSequentialDataBlock(0, [0] * DEFAULT_REGISTER_COUNT),  # Input Registers
    )

    return ModbusServerContext(devices=slave, single=True)


async def run_plc_server(
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    context: Optional[ModbusServerContext] = None,
) -> None:
    """Start the Modbus TCP server and block until cancelled."""
    if context is None:
        # Default initial values: everything zeroed, setpoint = 22.0 °C
        context = build_context({2: 220, 3: 215})

    logger.info("Starting PLC Simulator on %s:%d …", host, port)
    logger.info(
        "Holding-register snapshot (first 10): %s",
        context[0x00].getValues(3, 0, count=10),
    )

    await StartAsyncTcpServer(
        context=context,
        address=(host, port),
    )


def main() -> None:
    """CLI entry-point."""
    host = DEFAULT_HOST
    port = DEFAULT_PORT
    if "--port" in sys.argv:
        idx = sys.argv.index("--port")
        port = int(sys.argv[idx + 1])
    if "--host" in sys.argv:
        idx = sys.argv.index("--host")
        host = sys.argv[idx + 1]

    loop = asyncio.new_event_loop()

    def _shutdown(sig: Any, frame: Any) -> None:
        logger.info("Received signal %s — shutting down.", sig)
        for task in asyncio.all_tasks(loop):
            task.cancel()
        loop.stop()

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    try:
        loop.run_until_complete(run_plc_server(host=host, port=port))
    except asyncio.CancelledError:
        pass
    finally:
        loop.close()
        logger.info("PLC Simulator stopped.")


if __name__ == "__main__":
    main()
