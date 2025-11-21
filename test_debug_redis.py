#!/usr/bin/env python3
import os
import json
import asyncio
from pathlib import Path

# .env 로드
from dotenv import load_dotenv
load_dotenv(Path('.env'))

print('=== Debug Test ===')

from upstash_redis import Redis
from messaging.redis_signal_publisher import SignalPublisher

# Redis 직접 테스트
redis = Redis(
    url=os.environ['UPSTASH_REDIS_REST_URL'],
    token=os.environ['UPSTASH_REDIS_REST_TOKEN']
)

# xadd 직접 테스트
print('Testing direct xadd...')
try:
    result = redis.xadd('prism:trading-signals', {'data': json.dumps({'test': 'direct_test'})})
    print(f'Direct xadd result: {result}')
    print(f'Result type: {type(result)}')
except Exception as e:
    import traceback
    print(f'Direct xadd error: {type(e).__name__}: {e}')
    traceback.print_exc()

# SignalPublisher 테스트
publisher = SignalPublisher()
publisher._redis = redis
print(f'Publisher _redis is set: {publisher._is_connected()}')

async def test_publish():
    print('Testing publisher...')
    try:
        result = await publisher.publish_buy_signal(
            ticker='TEST001',
            company_name='테스트',
            price=10000
        )
        print(f'Publisher result: {result}')
        print(f'Result type: {type(result)}')
    except Exception as e:
        import traceback
        print(f'Publisher error: {type(e).__name__}: {e}')
        traceback.print_exc()

asyncio.run(test_publish())
print('=== Done ===')
