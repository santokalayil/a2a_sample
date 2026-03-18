import httpx
import asyncio

async def main():
    async with httpx.AsyncClient() as client:
        print("Sending initial message...")
        r1 = await client.post("http://localhost:8002/chat/debug11", json={"message": "Hello"})
        data = r1.json()
        task_id = data.get("task_id")
        print(f"Task ID: {task_id}")
        await asyncio.sleep(8)
        
        print("Sending continuation message...")
        r2 = await client.post("http://localhost:8002/chat/debug11", json={"message": "A", "task_id": task_id})
        print(r2.json())
        await asyncio.sleep(8)

asyncio.run(main())
