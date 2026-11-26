import unittest
from unittest.mock import AsyncMock, patch

import bridge_client


class WaitTests(unittest.IsolatedAsyncioTestCase):
    async def test_manual_pager_returns_without_waiting(self):
        result = {"status": "running", "waiting_for_input": True}
        with patch.object(bridge_client, "request", AsyncMock(return_value=result)) as request:
            with patch.object(bridge_client.asyncio, "sleep", AsyncMock()) as sleep:
                self.assertEqual(await bridge_client.wait_for_job("session", "job", 30), result)
        request.assert_awaited_once()
        sleep.assert_not_awaited()

    async def test_auto_paging_keeps_waiting_until_complete(self):
        replies = [{"status": "running", "waiting_for_input": False}, {"status": "completed"}]
        with patch.object(bridge_client, "request", AsyncMock(side_effect=replies)):
            with patch.object(bridge_client.asyncio, "sleep", AsyncMock()) as sleep:
                result = await bridge_client.wait_for_job("session", "job", 30)
        self.assertEqual(result["status"], "completed")
        sleep.assert_awaited_once_with(0.2)


if __name__ == "__main__":
    unittest.main()
