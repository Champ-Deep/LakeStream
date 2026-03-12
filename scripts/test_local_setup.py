"""Test local setup is working correctly."""
import asyncio
import httpx
from src.config.settings import get_settings
from src.db.pool import get_pool


async def test_database():
    """Test database connection."""
    try:
        pool = await get_pool()
        await pool.fetchval("SELECT 1")
        print("✅ Database connection: OK")
        return True
    except Exception as e:
        print(f"❌ Database connection: FAILED - {e}")
        return False


async def test_redis():
    """Test Redis connection."""
    try:
        import redis.asyncio as redis
        settings = get_settings()
        client = await redis.from_url(settings.redis_url)
        await client.ping()
        await client.aclose()
        print("✅ Redis connection: OK")
        return True
    except Exception as e:
        print(f"❌ Redis connection: FAILED - {e}")
        return False


async def test_api():
    """Test API is running."""
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get("http://localhost:3002/health")
            if response.status_code == 200:
                print("✅ API health check: OK")
                return True
            else:
                print(f"❌ API health check: FAILED - status {response.status_code}")
                return False
    except Exception as e:
        print(f"❌ API health check: FAILED - {e}")
        print("   💡 Make sure to run 'make dev' in another terminal")
        return False


async def main():
    print("🧪 Testing LakeStream Local Setup\n")

    results = await asyncio.gather(
        test_database(),
        test_redis(),
        test_api()
    )

    if all(results):
        print("\n🎉 All tests passed! Your local setup is ready.")
    else:
        print("\n⚠️  Some tests failed. Please check the errors above.")


if __name__ == "__main__":
    asyncio.run(main())
