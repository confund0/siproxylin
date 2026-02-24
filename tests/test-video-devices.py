#!/usr/bin/env python3
"""
Test video device enumeration via gRPC bridge.

This script tests the ListVideoDevices RPC call from Python → Go.
"""

import asyncio
import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from drunk_call_hook.bridge import CallBridge, GoCallService
import logging

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


async def test_video_devices():
    """Test video device enumeration."""

    # Start Go service
    go_service = GoCallService(logger=logger)
    logger.info("Starting Go service...")
    if not await go_service.start():
        logger.error("Failed to start Go service")
        return

    logger.info("Go service started successfully")

    try:
        # Create bridge
        bridge = CallBridge(logger=logger)
        await bridge.connect()

        # List video devices
        logger.info("Listing video devices...")
        devices = await bridge.list_video_devices()

        if not devices:
            logger.warning("No video devices found")
        else:
            logger.info(f"Found {len(devices)} video device(s):")
            for i, dev in enumerate(devices, 1):
                logger.info(f"  {i}. {dev['name']}")
                logger.info(f"     Path: {dev['device_path']}")
                logger.info(f"     Driver: {dev['driver']}")
                logger.info(f"     Bus: {dev['bus_info']}")

        # Cleanup
        await bridge.disconnect()

    finally:
        # Stop Go service
        logger.info("Stopping Go service...")
        await go_service.stop()
        logger.info("Test complete")


if __name__ == '__main__':
    asyncio.run(test_video_devices())
